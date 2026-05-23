"""Tests for the LLM debug callback handler + Settings round-trip.

The debug toggle in the Settings UI exports ``TRADINGAGENTS_LLM_DEBUG=1``
to subprocesses; the Gonka client reads that env var at LLM-construction
time and attaches :class:`LLMDebugLogger` to the chat-model callbacks.
These tests pin both halves of that contract.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents.llm_clients.debug_logging import (
    LLMDebugLogger,
    debug_logging_enabled,
)


# ---------------------------------------------------------------------------
# debug_logging_enabled (env-var dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDebugLoggingEnabled:
    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "ON"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("TRADINGAGENTS_LLM_DEBUG", value)
        assert debug_logging_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "garbage"])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("TRADINGAGENTS_LLM_DEBUG", value)
        assert debug_logging_enabled() is False

    def test_default_is_off_when_unset(self, monkeypatch):
        monkeypatch.delenv("TRADINGAGENTS_LLM_DEBUG", raising=False)
        assert debug_logging_enabled() is False


# ---------------------------------------------------------------------------
# LLMDebugLogger
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.unit
class TestLLMDebugLogger:
    def test_chat_call_produces_one_jsonl_line(self, tmp_path):
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)

        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            serialized={"kwargs": {
                "model": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
                "base_url": "https://api.gonkascan.com/v1",
                "max_tokens": 8192,
                "streaming": True,
                "frequency_penalty": 0.3,
            }},
            messages=[[
                SystemMessage(content="You are a market analyst."),
                HumanMessage(content="Write a report on AVGO."),
            ]],
            run_id=run_id,
            tags=["market_analyst"],
            metadata={"ticker": "AVGO"},
        )
        handler.on_llm_end(
            response=LLMResult(generations=[[
                ChatGeneration(
                    message=AIMessage(content="**Market Analysis Report**: ..."),
                    generation_info={"finish_reason": "stop"},
                ),
            ]], llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 250}}),
            run_id=run_id,
        )

        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["run_id"] == str(run_id)
        assert row["model"] == "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
        assert row["init_kwargs"]["frequency_penalty"] == 0.3
        assert row["init_kwargs"]["base_url"] == "https://api.gonkascan.com/v1"
        assert row["tags"] == ["market_analyst"]
        assert row["metadata"] == {"ticker": "AVGO"}
        # Messages came through with role normalisation (human -> user, ai -> assistant).
        roles = [m["role"] for m in row["messages"]]
        assert roles == ["system", "user"]
        assert row["messages"][1]["content"] == "Write a report on AVGO."
        # Response captured.
        assert row["response"]["generations"][0]["content"].startswith("**Market Analysis")
        assert row["response"]["llm_output"]["token_usage"]["completion_tokens"] == 250
        assert row["error"] is None

    def test_init_kwargs_whitelist_excludes_secrets(self, tmp_path):
        """Confirm api_key / http_client / private_key are never serialised."""
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            serialized={"kwargs": {
                "model": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
                "base_url": "https://api.gonkascan.com/v1",
                "api_key": "sk-supersecret",
                "http_client": object(),
                "max_tokens": 8192,
            }},
            messages=[[HumanMessage(content="hi")]],
            run_id=run_id,
        )
        handler.on_llm_end(
            response=LLMResult(generations=[[
                ChatGeneration(message=AIMessage(content="ok")),
            ]]),
            run_id=run_id,
        )
        blob = json.dumps(_read_jsonl(log_path)[0])
        assert "sk-supersecret" not in blob
        # init_kwargs should hold the whitelisted fields only.
        row = _read_jsonl(log_path)[0]
        assert "api_key" not in row["init_kwargs"]
        assert "http_client" not in row["init_kwargs"]

    def test_error_path_records_exception(self, tmp_path):
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)
        run_id = uuid.uuid4()
        handler.on_chat_model_start(
            serialized={"kwargs": {"model": "Qwen/..."}},
            messages=[[HumanMessage(content="hi")]],
            run_id=run_id,
        )
        handler.on_llm_error(error=RuntimeError("upstream 502"), run_id=run_id)
        row = _read_jsonl(log_path)[0]
        assert row["response"] is None
        assert row["error"]["type"] == "RuntimeError"
        assert row["error"]["message"] == "upstream 502"
        assert "elapsed_ms" in row

    def test_truncation_caps_huge_field(self, tmp_path):
        """A genuine token-salad loop fills the entire max_tokens budget;
        we cap each field at 200k chars so a single record stays bounded."""
        from tradingagents.llm_clients import debug_logging as mod

        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)
        run_id = uuid.uuid4()
        huge = "x" * (mod._FIELD_CHAR_CAP + 500)
        handler.on_chat_model_start(
            serialized={"kwargs": {"model": "m"}},
            messages=[[HumanMessage(content=huge)]],
            run_id=run_id,
        )
        handler.on_llm_end(
            response=LLMResult(generations=[[
                ChatGeneration(message=AIMessage(content=huge)),
            ]]),
            run_id=run_id,
        )
        row = _read_jsonl(log_path)[0]
        assert "[truncated;" in row["messages"][0]["content"]
        assert "[truncated;" in row["response"]["generations"][0]["content"]

    def test_completion_style_on_llm_start_recorded_as_user_message(self, tmp_path):
        """Some langchain wrappers call ``on_llm_start`` with raw prompt
        strings instead of ``on_chat_model_start``. We normalise both
        shapes into the same record."""
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)
        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"kwargs": {"model": "m"}},
            prompts=["raw prompt text"],
            run_id=run_id,
        )
        handler.on_llm_end(
            response=LLMResult(generations=[[
                ChatGeneration(message=AIMessage(content="response")),
            ]]),
            run_id=run_id,
        )
        row = _read_jsonl(log_path)[0]
        assert row["messages"] == [{"role": "user", "content": "raw prompt text"}]
        assert row["response"]["generations"][0]["content"] == "response"

    def test_concurrent_writes_do_not_interleave(self, tmp_path):
        """Parallel ticker workers share one handler; each emit() must
        produce a complete JSONL line. Stress with many concurrent
        invocations and assert the file parses cleanly."""
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)

        def fire():
            run_id = uuid.uuid4()
            handler.on_chat_model_start(
                serialized={"kwargs": {"model": "m"}},
                messages=[[HumanMessage(content=f"call {run_id}")]],
                run_id=run_id,
            )
            handler.on_llm_end(
                response=LLMResult(generations=[[
                    ChatGeneration(message=AIMessage(content="ok")),
                ]]),
                run_id=run_id,
            )

        threads = [threading.Thread(target=fire) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = _read_jsonl(log_path)
        assert len(rows) == 20
        # All run_ids unique → no records got merged.
        assert len({r["run_id"] for r in rows}) == 20

    def test_end_without_matching_start_still_logs(self, tmp_path):
        """Defensive: ``on_llm_end`` without a preceding start (e.g. when
        the callback was added mid-stream) must not crash; we still
        capture the tail."""
        log_path = tmp_path / "debug.jsonl"
        handler = LLMDebugLogger(log_path=log_path)
        run_id = uuid.uuid4()
        handler.on_llm_end(
            response=LLMResult(generations=[[
                ChatGeneration(message=AIMessage(content="ok")),
            ]]),
            run_id=run_id,
        )
        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        assert rows[0]["run_id"] == str(run_id)
        assert rows[0]["response"]["generations"][0]["content"] == "ok"


# ---------------------------------------------------------------------------
# GonkaClient: callback attached only when env var is set
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGonkaClientDebugWiring:
    """The Gonka client must attach LLMDebugLogger when the toggle is on,
    and must not attach it otherwise — the operator should not pay for
    debug logging by default."""

    def _build_router_llm(self, monkeypatch, env_value):
        from tradingagents.llm_clients import gonka_client as mod

        captured: dict = {}

        class _FakeChatOpenAI:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

        monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)
        for v in ("GONKA_API_KEY", "GONKA_PRIVATE_KEY", "GONKA_SOURCE_URL"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("GONKA_API_KEY", "router-key")
        if env_value is None:
            monkeypatch.delenv("TRADINGAGENTS_LLM_DEBUG", raising=False)
        else:
            monkeypatch.setenv("TRADINGAGENTS_LLM_DEBUG", env_value)

        mod.GonkaClient("moonshotai/Kimi-K2.6").get_llm()
        return captured["kwargs"]

    def test_callback_attached_when_toggle_on(self, monkeypatch):
        kwargs = self._build_router_llm(monkeypatch, "1")
        callbacks = kwargs.get("callbacks") or []
        assert any(isinstance(cb, LLMDebugLogger) for cb in callbacks)

    def test_callback_not_attached_when_toggle_off(self, monkeypatch):
        kwargs = self._build_router_llm(monkeypatch, "0")
        callbacks = kwargs.get("callbacks") or []
        assert not any(isinstance(cb, LLMDebugLogger) for cb in callbacks)

    def test_callback_not_attached_when_env_unset(self, monkeypatch):
        kwargs = self._build_router_llm(monkeypatch, None)
        callbacks = kwargs.get("callbacks") or []
        assert not any(isinstance(cb, LLMDebugLogger) for cb in callbacks)

    def test_existing_callbacks_preserved(self, monkeypatch):
        """If a caller passes ``callbacks=[their_handler]``, ours appends
        rather than replacing — we never trample a caller's instrumentation."""
        from tradingagents.llm_clients import gonka_client as mod

        captured: dict = {}

        class _FakeChatOpenAI:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

        class _CallerHandler:
            pass

        caller_handler = _CallerHandler()
        monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)
        for v in ("GONKA_API_KEY", "GONKA_PRIVATE_KEY", "GONKA_SOURCE_URL"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("GONKA_API_KEY", "router-key")
        monkeypatch.setenv("TRADINGAGENTS_LLM_DEBUG", "1")

        mod.GonkaClient(
            "moonshotai/Kimi-K2.6",
            callbacks=[caller_handler],
        ).get_llm()

        callbacks = captured["kwargs"]["callbacks"]
        assert caller_handler in callbacks
        assert any(isinstance(cb, LLMDebugLogger) for cb in callbacks)


# ---------------------------------------------------------------------------
# Settings round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsLlmDebugRoundTrip:
    def test_settings_to_env_emits_truthy_flag(self):
        from app.settings_store import settings_to_env

        env = settings_to_env({
            "mode": "router",
            "router_api_key": "k",
            "deep_model": "m",
            "quick_model": "m",
            "max_workers": 4,
            "llm_debug": True,
        })
        assert env["TRADINGAGENTS_LLM_DEBUG"] == "1"

    def test_settings_to_env_emits_falsy_when_disabled(self):
        from app.settings_store import settings_to_env

        env = settings_to_env({
            "mode": "router",
            "router_api_key": "k",
            "deep_model": "m",
            "quick_model": "m",
            "max_workers": 4,
            "llm_debug": False,
        })
        assert env["TRADINGAGENTS_LLM_DEBUG"] == "0"

    def test_settings_to_env_treats_missing_as_off(self):
        from app.settings_store import settings_to_env

        env = settings_to_env({
            "mode": "router",
            "router_api_key": "k",
            "deep_model": "m",
            "quick_model": "m",
            "max_workers": 4,
        })
        assert env["TRADINGAGENTS_LLM_DEBUG"] == "0"

    def test_public_view_exposes_llm_debug(self):
        from app.settings_store import public_view

        view = public_view({
            "mode": "router",
            "router_api_key": "k",
            "sdk_private_key": "",
            "sdk_source_url": "",
            "deep_model": "m",
            "quick_model": "m",
            "max_workers": 4,
            "llm_debug": True,
        })
        assert view["llm_debug"] is True

    def test_default_from_env_reads_truthy(self, monkeypatch):
        from app.settings_store import defaults_from_env

        monkeypatch.setenv("TRADINGAGENTS_LLM_DEBUG", "true")
        assert defaults_from_env()["llm_debug"] is True

    def test_default_from_env_reads_falsy(self, monkeypatch):
        from app.settings_store import defaults_from_env

        monkeypatch.delenv("TRADINGAGENTS_LLM_DEBUG", raising=False)
        assert defaults_from_env()["llm_debug"] is False
