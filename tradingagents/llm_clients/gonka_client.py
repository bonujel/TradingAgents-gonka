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


# Per-family ``max_tokens`` defaults. The two families that Gonka serves
# need different caps for two unrelated reasons:
#
# * **Kimi-K2.6** (reasoning model): streams CoT tokens through the SSE
#   channel as ``delta.content == ""`` chunks. LangChain's
#   ``generate_from_stream`` raises ``ValueError: No generations found in
#   stream`` (chat_models.py:223) when zero content chunks arrive, so the
#   cap must leave room for both the reasoning trace and the visible
#   answer. Empirical 2026-05-20: ~85% of completion tokens went to
#   reasoning; 8192 leaves ~2 k for visible output.
# * **Qwen3-235B-Instruct-2507-FP8** (non-reasoning): Gonka's FP8 serving
#   has a deterministic degeneration bug at certain ``max_completion_tokens``
#   values — verified 2026-05-23 that 256, 2048, 4000, 8000 and ≥8192 emit
#   byte-level garbage (``#(c(c(c…``, ``피피피``, ``/access随着时间…``) instead
#   of producing the requested output. 4096 was the empirically-safest
#   value in that sweep, and Qwen3-Instruct doesn't need the headroom that
#   Kimi needs because it has no internal CoT stream.
#
# Operators tune these per-family caps via Settings → "Qwen max tokens" /
# "Kimi max tokens"; the values are exported as TRADINGAGENTS_QWEN_MAX_TOKENS
# and TRADINGAGENTS_KIMI_MAX_TOKENS by ``app/settings_store.py``.
_DEFAULT_MAX_TOKENS_PER_FAMILY = {"qwen": 4096, "kimi": 8192}
_ENV_PER_FAMILY = {
    "qwen": "TRADINGAGENTS_QWEN_MAX_TOKENS",
    "kimi": "TRADINGAGENTS_KIMI_MAX_TOKENS",
}
_FALLBACK_MAX_TOKENS = 8192  # unknown model families (e.g. future additions)


def _max_tokens_for(model: str) -> int:
    name = (model or "").lower()
    for family, env_var in _ENV_PER_FAMILY.items():
        if family in name:
            raw = os.environ.get(env_var)
            if raw:
                try:
                    return int(raw)
                except ValueError:
                    logger.warning("Invalid %s=%r — using family default", env_var, raw)
            return _DEFAULT_MAX_TOKENS_PER_FAMILY[family]
    return _FALLBACK_MAX_TOKENS


# The centralised router endpoint. Hardcoded here rather than in
# ``openai_client._PROVIDER_BASE_URL`` because the Gonka provider does not go
# through the generic OpenAI-compatible code path — it has its own dispatch.
_ROUTER_BASE_URL = "https://api.gonkascan.com/v1"


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

    Structured-output (``with_structured_output``) is intentionally NOT
    overridden here. The three decision-making agents (Research Manager,
    Portfolio Manager, Trader) used to take a json_schema path, but Gonka's
    vLLM caps json_schema completions at ~3072 tokens — Kimi-K2.6's
    reasoning + JSON for those agents exceeds that limit, so every
    structured attempt failed length-limit and fell back to free text.
    The agents now emit free-text prose directly in a canonical shape
    that ``tradingagents.agents.utils.rating.parse_rating`` extracts the
    rating from. No code in this tree calls ``with_structured_output`` on
    a Gonka client, so we leave the langchain-openai default behaviour
    untouched for any future caller that wants it.
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
        only takes effect when streaming is on, so we mirror that. ``max_tokens``
        defaults to the per-family cap from :func:`_max_tokens_for` (read from
        ``TRADINGAGENTS_*_MAX_TOKENS`` env, populated by the Settings UI) — see
        the module-level block comment for the family-specific rationale.
        """
        for key, value in _STREAMING_DEFAULTS.items():
            llm_kwargs.setdefault(key, self.kwargs.get(key, value))
        llm_kwargs.setdefault(
            "max_tokens",
            self.kwargs.get("max_tokens", _max_tokens_for(self.model)),
        )

    def _attach_debug_logger(self, llm_kwargs: dict[str, Any]) -> None:
        """When the operator flips the ``Capture LLM debug log`` toggle in
        Settings (which exports ``TRADINGAGENTS_LLM_DEBUG=1``), attach an
        :class:`LLMDebugLogger` to ``callbacks`` so every chat-model
        invocation through this LLM lands as a line in
        ``~/.tradingagents/app/logs/llm_debug.jsonl``.

        Existing user-supplied callbacks are preserved — the debug logger
        is appended, not substituted.
        """
        from .debug_logging import LLMDebugLogger, debug_logging_enabled

        if not debug_logging_enabled():
            return
        existing = llm_kwargs.get("callbacks") or []
        llm_kwargs["callbacks"] = [*existing, LLMDebugLogger()]

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
        self._attach_debug_logger(llm_kwargs)
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
        self._attach_debug_logger(llm_kwargs)
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
