"""LLM client for Gonka with two interchangeable transport modes.

Gonka exposes two ways to talk to the network:

**SDK / decentralised mode** — the ``gonka-openai`` SDK signs every request with
an ECDSA secp256k1 key. ``GONKA_SOURCE_URL`` should point at a public inference
gateway (e.g. ``https://node4.gonka.ai``); we GET ``{source}/v1/identity`` to
discover the gateway's whitelisted ``transfer_address`` and route the signed
request through ``{source}/v1``. The signing key pays; the gateway's identity
satisfies the on-chain ``transfer_agent_access_params`` whitelist.

**Router mode** — ``api.gonkascan.com/v1`` is a centralised OpenAI-compatible
proxy that performs the signing on the user's behalf; clients just present a
bearer token issued by the routerscan dashboard. Operationally identical to
hitting OpenAI directly.

This client auto-selects between the two based on which env vars are present:

  * ``GONKA_PRIVATE_KEY`` + ``GONKA_SOURCE_URL`` → SDK mode (preferred)
  * ``GONKA_API_KEY``                           → router mode (fallback)
  * neither                                     → raise with a helpful message

Both modes return the same ``NormalizedChatOpenAI`` wrapper used by every other
OpenAI-compatible provider, so structured-output / tool-calling / capability
dispatch keep working unchanged downstream.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .base_client import BaseLLMClient
from .openai_client import NormalizedChatOpenAI
from .validators import validate_model

logger = logging.getLogger(__name__)


# Forwarded verbatim to ``ChatOpenAI`` so callers can tune timeouts and retries
# the same way they do for the other OpenAI-compatible providers.
_PASSTHROUGH_KWARGS = ("timeout", "max_retries", "callbacks", "streaming")


# Default kwargs that apply to both SDK and router paths. Streaming is on by
# default because:
#   1. The Gonka API node proxies streamed responses chunk-by-chunk
#      (decentralized-api/internal/server/public/proxy.go: text/event-stream
#      branch uses bufio.Scanner + Fprintln), but buffers non-streamed JSON
#      responses entirely (io.ReadAll). For long-running inferences the
#      non-streamed path leaves the TCP connection silent until the executor
#      finishes — Cloudflare's 100s upstream-response timeout fires every time
#      Kimi-K2.6 (or any sufficiently long generation) crosses that
#      threshold. Streaming keeps bytes flowing so CF never sees the gap.
#   2. ``stream_usage=True`` re-asks the server to include the usage block in
#      the final stream chunk; otherwise LangChain has no way to report
#      token counts under streaming mode.
# Operators who really need non-streamed (e.g. for an upstream that doesn't
# support SSE) can pass ``streaming=False`` via kwargs.
_STREAMING_DEFAULTS = {"streaming": True, "stream_usage": True}


# Generation-budget defaults applied to every Gonka call regardless of
# transport. ``max_tokens=8192`` is set high enough that reasoning models
# (e.g. Kimi-K2.6) don't burn their entire budget on internal CoT before
# producing a single visible chunk. Background:
#   * Reasoning models stream their CoT tokens through the same SSE channel
#     as the visible answer, but with empty ``delta.content``. LangChain's
#     ``generate_from_stream`` raises ``ValueError: No generations found
#     in stream`` when zero content chunks arrive (chat_models.py:223).
#   * Empirical 2026-05-20 batch on Kimi: ~85% of completion tokens went to
#     reasoning. A 1024-token cap (or any other low default) left near-zero
#     budget for visible output → the ValueError fired for every retry.
# 8192 is loose enough for Kimi's reasoning + ~2k visible answer; non-
# reasoning models (Qwen3-Instruct) simply ignore the headroom — they stop
# at finish_reason='stop' well below the cap, so there's no cost impact.
_GENERATION_DEFAULTS = {"max_tokens": 8192}


# The centralised router endpoint. Hardcoded here rather than in
# ``openai_client._PROVIDER_BASE_URL`` because the Gonka provider does not go
# through the generic OpenAI-compatible code path — it has its own dispatch.
_ROUTER_BASE_URL = "https://api.gonkascan.com/v1"


# Class-level cache of (model_name, base_url) pairs that have been
# observed to fail the function_calling structured-output path with the
# specific vLLM "auto tool choice requires --enable-auto-tool-choice
# and --tool-call-parser" error. Entries persist for the process
# lifetime only — restarting picks up upstream fixes automatically.
# We deliberately do not persist this to disk: when the Gonka model
# operators add the flags, a normal process restart resumes
# function_calling with no code or config change on the client side.
_BACKENDS_WITHOUT_TOOL_CALLING: set[tuple[str, str]] = set()

# Substring uniquely identifying the vLLM serving-chat refusal. This is
# the literal wording from vllm/entrypoints/openai/serving_chat.py —
# narrow enough not to false-positive on generic 400s, broad enough to
# survive minor wording changes across vLLM versions. If vLLM ever
# rewords this line, the wrapper will stop downgrading and the original
# exception will propagate; the fix is updating this one constant.
_VLLM_TOOL_CHOICE_ERROR_HINT = "tool choice requires --enable-auto-tool-choice"


class _LearningStructuredRunnable:
    """Wraps a structured-output runnable so the first invocation can
    detect a backend that lacks working vLLM tool-calling, rebind the
    schema with json_mode, and cache that discovery for the rest of the
    process.

    Two failure modes trigger the downgrade, both rooted in incomplete
    tool-calling support on the Gonka vLLM backend:

    * **Hard failure** — vLLM rejects the request with a 400 whose body
      contains ``tool choice requires --enable-auto-tool-choice``.
    * **Silent failure** — the backend accepts the request but the model
      (notably the Kimi reasoning models) answers in plain prose without
      emitting the forced tool call. LangChain's tool parser then yields
      ``None``. function_calling can never succeed on such a backend, so
      a ``None`` return is treated exactly like the hard failure.

    See dev_notes/gonka-structured-output-resilience-design.md
    (Component 1) for the full design rationale.
    """

    def __init__(self, primary, host, schema, extra_kwargs):
        self._primary = primary
        self._host = host
        self._schema = schema
        self._extra_kwargs = extra_kwargs
        self._downgraded = None  # built lazily on first downgrade

    def _build_json_mode(self):
        # Bypass GonkaStreamSafeChatOpenAI.with_structured_output to
        # avoid re-wrapping ourselves recursively. Reaching directly
        # into NormalizedChatOpenAI gives us a plain json_mode binding.
        return NormalizedChatOpenAI.with_structured_output(
            self._host, self._schema, method="json_mode", **self._extra_kwargs
        )

    def _downgrade(self, reason: str) -> None:
        """Log once, cache the discovery, and build the json_mode runnable."""
        logger.warning(
            "%s on %s: %s; downgrading structured output to json_mode for "
            "the lifetime of this process. Next process start will re-probe "
            "— if the backend has been fixed, function_calling resumes "
            "automatically.",
            self._host.model_name, self._host.openai_api_base, reason,
        )
        _BACKENDS_WITHOUT_TOOL_CALLING.add(
            (self._host.model_name, str(self._host.openai_api_base or ""))
        )
        self._downgraded = self._build_json_mode()

    def invoke(self, prompt, *args, **kwargs):
        if self._downgraded is not None:
            return self._downgraded.invoke(prompt, *args, **kwargs)
        try:
            result = self._primary.invoke(prompt, *args, **kwargs)
        except Exception as exc:
            if _VLLM_TOOL_CHOICE_ERROR_HINT not in str(exc):
                raise
            self._downgrade(
                f"vLLM rejected tool-calling structured output ({exc})"
            )
            return self._downgraded.invoke(prompt, *args, **kwargs)
        if result is not None:
            return result
        # The backend accepted the request but the model emitted no tool
        # call, so LangChain's tool parser returned None. Same root cause
        # as the 400 above (incomplete tool-calling support); downgrade
        # and retry this same prompt on json_mode.
        self._downgrade(
            "structured-output call returned no tool call (parser yielded None)"
        )
        return self._downgraded.invoke(prompt, *args, **kwargs)


class GonkaStreamSafeChatOpenAI(NormalizedChatOpenAI):
    """vLLM-aware ChatOpenAI subclass for Gonka backends.

    Gonka serves inference through vLLM, which is stricter than OpenAI's own
    chat-completions API about assistant message content:

    * OpenAI accepts ``content: ""`` (or whitespace-only content) on
      assistant messages that carry ``tool_calls`` — the empty content is
      treated as "tool call only".
    * vLLM rejects the same payload with HTTP 400 ``messages[i].content:
      must not be empty`` — both the empty string and an all-whitespace
      string (e.g. ``"\\n\\n"``) trip it.

    Under streaming mode the chunk aggregator inside ``langchain-openai``
    naturally produces these shapes: when a streamed assistant turn finishes
    with only tool calls and a few newline/space chunks in between, the
    aggregated message lands with ``content="\\n\\n\\n"``. On the *next*
    turn — when LangChain ships the entire history back — vLLM 400s.

    The fix is purely in the outbound direction: right before the openai SDK
    serialises the message list, we rewrite any assistant-with-tool_calls
    message whose content stripped to empty into ``content=None`` (which the
    SDK turns into JSON ``null`` — the form vLLM accepts). Non-assistant
    roles and assistant messages with real content are left untouched.
    """

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message in payload.get("messages", []):
            if message.get("role") != "assistant":
                continue
            if not message.get("tool_calls"):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip() == "":
                message["content"] = None
        return payload

    def with_structured_output(self, schema, *, method=None, **kwargs):
        # Self-healing structured-output dispatch — see
        # dev_notes/gonka-structured-output-resilience-design.md,
        # Component 1. When we already know this backend rejects
        # tool-calling requests, ask the superclass to bind a json_mode
        # runnable directly. Either way, wrap the result so a *new*
        # backend (or a backend that has just been fixed upstream) is
        # discovered on first invoke without any client-side change.
        cache_key = (self.model_name, str(self.openai_api_base or ""))
        if method is None and cache_key in _BACKENDS_WITHOUT_TOOL_CALLING:
            method = "json_mode"
        primary = super().with_structured_output(schema, method=method, **kwargs)
        return _LearningStructuredRunnable(
            primary=primary,
            host=self,
            schema=schema,
            extra_kwargs=kwargs,
        )


class GonkaClient(BaseLLMClient):
    """LLM client for Gonka. Auto-selects SDK vs router transport at runtime.

    Construction is lazy in ``get_llm()`` — importing ``gonka_openai`` at module
    import time would force every test that touches the factory to install the
    SDK plus its ``secp256k1`` C dependency. The lazy import also means the
    router-only path keeps working without ``gonka-openai`` installed at all.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = "gonka"

    # ── transport detection ────────────────────────────────────────────────

    def _sdk_source_url(self) -> Optional[str]:
        """Return the source URL for SDK mode, if one is configured.

        ``base_url`` constructor arg wins over env. We treat an explicit
        ``api.gonkascan.com`` value as *not* a source URL — it's the router
        endpoint, and forwarding it to ``resolve_and_select_endpoint()`` would
        produce a confusing error. Operators who pass it really mean router.
        """
        candidate = self.base_url or os.environ.get("GONKA_SOURCE_URL")
        if not candidate:
            return None
        if "api.gonkascan.com" in candidate:
            return None
        return candidate

    def _sdk_private_key(self) -> Optional[str]:
        return os.environ.get("GONKA_PRIVATE_KEY") or None

    def _router_api_key(self) -> Optional[str]:
        return os.environ.get("GONKA_API_KEY") or None

    # ── builders ───────────────────────────────────────────────────────────

    def _apply_streaming_defaults(self, llm_kwargs: dict[str, Any]) -> None:
        """Layer streaming + generation-budget defaults under user overrides.

        Caller-passed kwargs win — operators who pass ``streaming=False`` or
        a custom ``max_tokens`` get their value through. ``stream_usage``
        only takes effect when streaming is on, so we mirror that. See the
        ``_STREAMING_DEFAULTS`` and ``_GENERATION_DEFAULTS`` block comments
        above for the rationale behind each default.
        """
        for defaults in (_STREAMING_DEFAULTS, _GENERATION_DEFAULTS):
            for key, value in defaults.items():
                llm_kwargs.setdefault(key, self.kwargs.get(key, value))

    def _build_sdk_llm(self, source_url: str, private_key: str) -> Any:
        import httpx
        from gonka_openai import gonka_http_client

        # Treat GONKA_SOURCE_URL as a public inference gateway and ask it
        # who it is via /v1/identity. The returned 'data.address' is the
        # gateway's on-chain transfer-agent identity — one of the seven
        # addresses in transfer_agent_access_params.allowed_transfer_addresses.
        # The user's own private key still signs every request (and pays),
        # but the transfer_address that participants validate against is the
        # gateway's, not the user's. This matches the SDK's official
        # quickstart and avoids two pitfalls of resolve_and_select_endpoint:
        #   - chain-api participant enumeration, which the public gateway
        #     domain (node4.gonka.ai) does not expose
        #   - random participant selection, which can land on a stale node
        #     whose registered URL 308-redirects POST requests
        src = source_url.rstrip("/")
        try:
            resp = httpx.get(f"{src}/v1/identity", timeout=30)
            resp.raise_for_status()
            transfer_address = resp.json()["data"]["address"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ValueError(
                f"Failed to fetch transfer-agent identity from "
                f"{src}/v1/identity: {exc}. GONKA_SOURCE_URL must point at "
                f"a Gonka public inference gateway (e.g. https://node4.gonka.ai)."
            ) from exc

        http_client = gonka_http_client(
            private_key=private_key,
            transfer_address=transfer_address,
        )
        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "base_url": f"{src}/v1",
            "http_client": http_client,
            # OpenAI SDK rejects construction without an api_key, but the
            # signed request bypasses bearer-token auth on the wire.
            "api_key": self.kwargs.get("api_key", "gonka-network"),
        }
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        self._apply_streaming_defaults(llm_kwargs)
        return GonkaStreamSafeChatOpenAI(**llm_kwargs)

    def _build_router_llm(self, api_key: str) -> Any:
        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "base_url": _ROUTER_BASE_URL,
            "api_key": api_key,
        }
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        self._apply_streaming_defaults(llm_kwargs)
        return GonkaStreamSafeChatOpenAI(**llm_kwargs)

    # ── public API ─────────────────────────────────────────────────────────

    def get_llm(self) -> Any:
        self.warn_if_unknown_model()

        source_url = self._sdk_source_url()
        private_key = self._sdk_private_key()
        if source_url and private_key:
            return self._build_sdk_llm(source_url, private_key)

        api_key = self._router_api_key()
        if api_key:
            return self._build_router_llm(api_key)

        # Distinguish "partial SDK config" from "nothing set" so the error
        # tells the operator which env var they actually forgot.
        if source_url and not private_key:
            raise ValueError(
                "GONKA_SOURCE_URL is set but GONKA_PRIVATE_KEY is missing — "
                "the SDK path needs both. Either set GONKA_PRIVATE_KEY or "
                "unset GONKA_SOURCE_URL and provide GONKA_API_KEY to use the "
                "centralised router instead."
            )
        if private_key and not source_url:
            raise ValueError(
                "GONKA_PRIVATE_KEY is set but GONKA_SOURCE_URL is missing — "
                "the SDK path needs both. Either set GONKA_SOURCE_URL (a "
                "Gonka network node URL) or unset GONKA_PRIVATE_KEY and "
                "provide GONKA_API_KEY to use the centralised router instead."
            )
        raise ValueError(
            "Gonka requires either GONKA_PRIVATE_KEY + GONKA_SOURCE_URL "
            "(decentralised SDK path) or GONKA_API_KEY (centralised router "
            "path at api.gonkascan.com). Set one combination in your env."
        )

    def validate_model(self) -> bool:
        return validate_model(self.provider, self.model)
