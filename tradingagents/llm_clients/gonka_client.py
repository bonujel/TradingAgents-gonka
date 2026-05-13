"""LLM client for Gonka with two interchangeable transport modes.

Gonka exposes two ways to talk to the network:

**SDK / decentralised mode** — the ``gonka-openai`` SDK signs every request with
an ECDSA secp256k1 key, derives the requester's gonka address via bech32, and
discovers a backing endpoint from a ``source_url`` (a Gonka network node). This
is the "Gonka-native" path.

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

import os
from typing import Any, Optional

from .base_client import BaseLLMClient
from .openai_client import NormalizedChatOpenAI
from .validators import validate_model


# Forwarded verbatim to ``ChatOpenAI`` so callers can tune timeouts and retries
# the same way they do for the other OpenAI-compatible providers.
_PASSTHROUGH_KWARGS = ("timeout", "max_retries", "callbacks")


# The centralised router endpoint. Hardcoded here rather than in
# ``openai_client._PROVIDER_BASE_URL`` because the Gonka provider does not go
# through the generic OpenAI-compatible code path — it has its own dispatch.
_ROUTER_BASE_URL = "https://api.gonkascan.com/v1"


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

    def _build_sdk_llm(self, source_url: str, private_key: str) -> Any:
        from gonka_openai import gonka_http_client, resolve_and_select_endpoint

        _endpoints, selected = resolve_and_select_endpoint(source_url=source_url)
        http_client = gonka_http_client(
            private_key=private_key,
            transfer_address=selected.address,
        )
        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "base_url": selected.url,
            "http_client": http_client,
            # OpenAI SDK rejects construction without an api_key, but the
            # signed request bypasses bearer-token auth on the wire.
            "api_key": self.kwargs.get("api_key", "gonka-network"),
        }
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        return NormalizedChatOpenAI(**llm_kwargs)

    def _build_router_llm(self, api_key: str) -> Any:
        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "base_url": _ROUTER_BASE_URL,
            "api_key": api_key,
        }
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        return NormalizedChatOpenAI(**llm_kwargs)

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
