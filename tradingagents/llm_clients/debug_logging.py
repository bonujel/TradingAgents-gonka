"""LangChain callback that records every chat-model invocation to JSONL.

The handler exists for one purpose: when Qwen (or any other Gonka-served
model) emits token salad or repetition loops, the operator needs the
exact prompt, the exact generation parameters, and the exact response so
the failure can be reproduced offline against the same executor. The
runner logs and the dashboard's saved reports both show the **rendered**
output but not the request — by the time degeneracy reaches the UI, the
information needed to reproduce it is already lost.

The handler is attached automatically in :mod:`gonka_client` whenever the
``TRADINGAGENTS_LLM_DEBUG`` environment variable is truthy. The settings
UI surfaces a checkbox that flips that env var; the dashboard's
``settings_to_env`` writes it on every run-spawn. Operators who want to
attach it from a different transport can import :class:`LLMDebugLogger`
directly and pass it via ``callbacks=[...]``.

Records are one JSON object per chat-model invocation, appended atomically
to ``~/.tradingagents/app/logs/llm_debug.jsonl``. Each record carries
``run_id`` (the LangChain UUID) and ``parent_run_id`` so chains can be
reconstructed; the prompt list and response are written verbatim, with a
soft size cap to keep a runaway loop from filling the disk.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Default location. ``app.settings_store`` writes scheduled-run logs under
# the same parent directory, so flushing the dashboard's "clear logs"
# button removes the debug JSONL alongside the existing run logs without
# needing a separate maintenance step.
_DEFAULT_LOG_PATH = Path.home() / ".tradingagents" / "app" / "logs" / "llm_debug.jsonl"

# Cap per-record message and response bodies. A genuine token-salad loop
# fills the entire max_tokens budget; without a cap one bad call could
# write ~50 KB+ per record. 200 000 chars per field is enough to capture
# any realistic prompt or completion in full while still bounding a
# single record at ~half a megabyte.
_FIELD_CHAR_CAP = 200_000


def debug_logging_enabled() -> bool:
    """Whether the env-var toggle is currently truthy.

    Read per call so the toggle takes effect on the next LLM build without
    a process restart.
    """
    return (
        os.environ.get("TRADINGAGENTS_LLM_DEBUG", "0").strip().lower() in _TRUTHY
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _truncate(value: Any) -> Any:
    """Clamp a single string field at ``_FIELD_CHAR_CAP`` characters.

    Returns the original value when it is not a string, when it is short
    enough, or when truncation is unnecessary. When trimmed, appends a
    marker so consumers do not silently parse a truncated body.
    """
    if not isinstance(value, str) or len(value) <= _FIELD_CHAR_CAP:
        return value
    return value[:_FIELD_CHAR_CAP] + f"\n…[truncated; {len(value)} chars total]"


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Best-effort serialise a langchain message (or dict) to a {role, content} dict."""
    if isinstance(message, dict):
        return {
            "role": message.get("role") or message.get("type") or "unknown",
            "content": _truncate(message.get("content")),
            **(
                {"name": message["name"]}
                if isinstance(message.get("name"), str) else {}
            ),
            **(
                {"tool_calls": message["tool_calls"]}
                if message.get("tool_calls") else {}
            ),
        }
    # LangChain BaseMessage instances expose ``type`` (system/human/ai/tool)
    # and ``content``; we map ``type`` to the OpenAI-style ``role`` name.
    role_map = {"human": "user", "ai": "assistant"}
    raw_role = getattr(message, "type", None) or message.__class__.__name__
    role = role_map.get(raw_role, raw_role)
    record: dict[str, Any] = {
        "role": role,
        "content": _truncate(getattr(message, "content", None)),
    }
    name = getattr(message, "name", None)
    if isinstance(name, str):
        record["name"] = name
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        record["tool_calls"] = tool_calls
    additional = getattr(message, "additional_kwargs", None)
    if additional:
        record["additional_kwargs"] = additional
    return record


def _flatten_messages(messages: Any) -> list[dict[str, Any]]:
    """LangChain hands the callback either ``list[list[BaseMessage]]``
    (chat models) or a flat list (older shapes). Flatten + serialise."""
    out: list[dict[str, Any]] = []
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, list):
                for inner in item:
                    out.append(_message_to_dict(inner))
            else:
                out.append(_message_to_dict(item))
    return out


class LLMDebugLogger(BaseCallbackHandler):
    """Atomic JSONL recorder for every chat-model invocation.

    Thread-safe: parallel ticker workers share one handler instance, so
    ``self._inflight`` and the file append are guarded by a single lock.
    Each invocation produces exactly one JSONL line, written on
    ``on_llm_end`` / ``on_llm_error`` once the full response is in hand.
    """

    def __init__(self, log_path: Path | str | None = None) -> None:
        super().__init__()
        self._path = Path(log_path) if log_path is not None else _DEFAULT_LOG_PATH
        self._lock = threading.Lock()
        # run_id -> partial record assembled at on_chat_model_start /
        # on_llm_start and completed at on_llm_end / on_llm_error.
        self._inflight: dict[Any, dict[str, Any]] = {}

    # ── helpers ───────────────────────────────────────────────────────────

    def _begin(
        self,
        run_id: Any,
        parent_run_id: Any,
        serialized: dict[str, Any] | None,
        messages: list[dict[str, Any]],
        tags: list[str] | None,
        metadata: dict[str, Any] | None,
        invocation_params: dict[str, Any] | None,
    ) -> None:
        # Serialised model spec: langchain stuffs the underlying ChatOpenAI's
        # init kwargs into ``serialized["kwargs"]``, which is the cleanest
        # source for the model name + base_url + the generation params we
        # actually sent (max_tokens, streaming, frequency_penalty, ...).
        model_kwargs = (serialized or {}).get("kwargs", {}) or {}
        record = {
            "ts_start": _now_iso(),
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "thread": threading.current_thread().name,
            "tags": list(tags) if tags else [],
            "metadata": metadata or {},
            "model": model_kwargs.get("model") or model_kwargs.get("model_name"),
            "base_url": model_kwargs.get("base_url"),
            "init_kwargs": {
                # Whitelist so we don't accidentally log api_key or http_client.
                k: model_kwargs.get(k)
                for k in (
                    "model", "model_name", "base_url", "streaming",
                    "stream_usage", "max_tokens", "temperature", "top_p",
                    "frequency_penalty", "presence_penalty",
                    "extra_body", "model_kwargs", "timeout", "max_retries",
                )
                if k in model_kwargs
            },
            "invocation_params": invocation_params or {},
            "messages": messages,
            "_started_at_monotonic": time.monotonic(),
        }
        with self._lock:
            self._inflight[run_id] = record

    def _finish(self, run_id: Any, *, response: Any = None, error: BaseException | None = None) -> None:
        with self._lock:
            record = self._inflight.pop(run_id, None)
        if record is None:
            # ``on_llm_*`` may fire without a matching start (e.g. when
            # a callback is added mid-stream) — best-effort log the tail.
            record = {
                "ts_start": None,
                "run_id": str(run_id),
                "messages": [],
                "_started_at_monotonic": None,
            }
        record["ts_end"] = _now_iso()
        started = record.pop("_started_at_monotonic", None)
        if started is not None:
            record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "message": _truncate(str(error)),
            }
            record["response"] = None
        else:
            record["error"] = None
            record["response"] = _response_to_dict(response)
        self._write(record)

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # POSIX append: each write() under O_APPEND is atomic up to
            # PIPE_BUF (4 KB on Linux), and our line is typically much
            # larger — so we still hold the lock to keep concurrent
            # callers' lines from interleaving.
            with self._lock:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.write("\n")
        except OSError as exc:
            # A debug logger should never break the run. Log to the
            # configured Python logger so the operator sees the failure
            # but the LLM call itself continues.
            logger.warning("LLMDebugLogger failed to write %s: %s", self._path, exc)

    # ── BaseCallbackHandler hooks ────────────────────────────────────────
    #
    # We override both ``on_chat_model_start`` and ``on_llm_start`` because
    # langchain dispatches to the former for chat models and the latter
    # for completion-style models; either may fire depending on the
    # caller's wrapper.

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # Some langchain versions pass invocation params under
        # ``kwargs["invocation_params"]`` instead of as a top-level arg.
        params = invocation_params or kwargs.get("invocation_params") or {}
        self._begin(
            run_id, parent_run_id, serialized,
            _flatten_messages(messages), tags, metadata, params,
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        params = invocation_params or kwargs.get("invocation_params") or {}
        # Treat raw completion prompts as a single user message so the
        # downstream record shape is uniform with chat models.
        messages = [{"role": "user", "content": _truncate(p)} for p in (prompts or [])]
        self._begin(run_id, parent_run_id, serialized, messages, tags, metadata, params)

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._finish(run_id, response=response)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._finish(run_id, error=error)


def _response_to_dict(response: Any) -> dict[str, Any] | None:
    """Serialise a langchain ``LLMResult`` (or compatible) into a record.

    Captures content, finish_reason, and usage. Streaming and non-streaming
    responses converge into the same shape here because by the time
    ``on_llm_end`` fires, the streamed chunks have already been aggregated
    into ``response.generations``.
    """
    if response is None:
        return None
    out: dict[str, Any] = {
        "generations": [],
    }
    generations = getattr(response, "generations", None) or []
    for batch in generations:
        for gen in batch:
            text = getattr(gen, "text", None)
            message = getattr(gen, "message", None)
            content = text if text is not None else (
                getattr(message, "content", None) if message is not None else None
            )
            entry = {
                "content": _truncate(content),
                "generation_info": getattr(gen, "generation_info", None),
            }
            if message is not None:
                # Surface tool_calls and additional_kwargs (reasoning
                # content lives here for DeepSeek) so the JSONL captures
                # everything the framework saw.
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                additional = getattr(message, "additional_kwargs", None)
                if additional:
                    entry["additional_kwargs"] = additional
                response_metadata = getattr(message, "response_metadata", None)
                if response_metadata:
                    entry["response_metadata"] = response_metadata
            out["generations"].append(entry)

    llm_output = getattr(response, "llm_output", None)
    if llm_output:
        out["llm_output"] = llm_output
    return out
