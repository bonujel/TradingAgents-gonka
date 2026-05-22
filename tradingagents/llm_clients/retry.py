"""Transient-failure classification for Gonka inference calls.

A single predicate, :func:`is_transient_llm_error`, shared by two retry
layers so they always agree on what counts as a recoverable upstream
blip versus a genuine fault:

* **Node-level** — ``tradingagents/graph/setup.py`` attaches a LangGraph
  ``RetryPolicy`` to every node, so a transient failure re-runs only the
  failing node and keeps every prior node's committed state.
* **Ticker-level** — ``app/runner.py`` wraps the whole ``propagate()``
  call as a coarse backstop for anything the node layer cannot recover.

The whitelist errs on the side of *not* retrying anything that smells
like a code bug or a malformed request — failing fast there surfaces
real problems instead of burning budget hiding them.
"""

from __future__ import annotations

import re

import httpx
from openai import APIConnectionError, APIError, InternalServerError, RateLimitError


# Exception *types* that are always a transient transport failure.
_TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,    # peer closed connection mid-stream (router/CDN)
    APIConnectionError,           # client-to-router TCP/socket failure
    InternalServerError,          # 5xx upstream (e.g. nginx 502 Bad Gateway)
    RateLimitError,               # 429 — backoff widens, so a retry is fine
)

# Bare openai.APIError is a catch-all: Gonka raises it for chain-executor
# failures with a plain-text message. Only the specific markers below are
# retried so future, genuinely-fatal APIError variants are not masked.
_API_ERROR_MARKERS: tuple[str, ...] = (
    "nonce_finished=false",
    "winner inference incomplete",
    # SDK stream watchdog: "winner stalled waiting for next chunk after
    # 1m3s" — an executor stopped sending tokens mid-stream.
    "stalled waiting for next chunk",
)

# Stream-level transient markers carried on ValueErrors.
_VALUE_ERROR_MARKERS: tuple[str, ...] = (
    # An empty stream is almost always the tail of an upstream disconnect
    # that did not surface as RemoteProtocolError.
    "No generations found in stream",
    # StructuredOutputEmpty from tradingagents/agents/utils/structured.py —
    # Trader / RM / PM produced no usable content, typically from upstream
    # stream truncation.
    "no usable content from structured output",
)

# A bare APIError whose message is a JSON decode failure means the openai
# SDK could not parse a streamed SSE ``data:`` chunk — the upstream proxy
# delivered a truncated or corrupted line. A cousin of RemoteProtocolError
# (the stream broke) without a clean connection close, so equally safe to
# retry. Every Python json.JSONDecodeError stringifies with this
# distinctive "line N column N (char N)" suffix regardless of the specific
# parse fault, which makes one regex more robust than enumerating each
# message prefix.
_JSON_DECODE_ERROR_RE = re.compile(r"line \d+ column \d+ \(char \d+\)")


def is_transient_llm_error(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is a recoverable upstream/transport blip.

    Retried (whitelist):
      - ``httpx.RemoteProtocolError``  — peer closed a chunked stream
      - ``openai.APIConnectionError``  — client TCP/socket error
      - ``openai.InternalServerError`` — 5xx upstream
      - ``openai.RateLimitError``      — 429
      - bare ``openai.APIError`` matching a Gonka chain marker
      - bare ``openai.APIError`` carrying a JSON-decode failure (corrupted
        SSE chunk from the upstream proxy)
      - ``ValueError`` matching a known empty-/truncated-stream marker

    Not retried (intentional):
      - ``langgraph`` ``GraphRecursionError`` — re-running hits the same cap
      - ``openai.BadRequestError`` / ``AuthenticationError`` / other
        ``APIStatusError`` subclasses — the request itself is wrong
      - any other ``ValueError`` / ``KeyError`` / ``TypeError`` — code bug
    """
    if isinstance(exc, _TRANSIENT_EXC):
        return True
    # Strict type check (not isinstance): only the bare APIError base class
    # carries Gonka's chain-level errors. APIStatusError subclasses
    # (BadRequestError, AuthenticationError, ...) inherit from APIError but
    # are intentionally excluded — their failure mode is not transient.
    if type(exc) is APIError:
        msg = str(exc)
        if any(m in msg for m in _API_ERROR_MARKERS):
            return True
        if _JSON_DECODE_ERROR_RE.search(msg):
            return True
    if isinstance(exc, ValueError):
        msg = str(exc)
        if any(m in msg for m in _VALUE_ERROR_MARKERS):
            return True
    return False
