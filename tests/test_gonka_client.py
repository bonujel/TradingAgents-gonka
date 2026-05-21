"""Unit tests for Gonka-specific LLM construction defaults."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx

from tradingagents.llm_clients import gonka_client as mod


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _clear_gonka_env(monkeypatch) -> None:
    for env_var in ("GONKA_API_KEY", "GONKA_PRIVATE_KEY", "GONKA_SOURCE_URL"):
        monkeypatch.delenv(env_var, raising=False)


def test_router_llm_sets_kimi_safe_generation_defaults(monkeypatch):
    _clear_gonka_env(monkeypatch)
    monkeypatch.setenv("GONKA_API_KEY", "router-key")
    monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)

    llm = mod.GonkaClient("moonshotai/Kimi-K2.6").get_llm()

    assert llm.kwargs["base_url"] == "https://api.gonkascan.com/v1"
    assert llm.kwargs["api_key"] == "router-key"
    assert llm.kwargs["streaming"] is True
    assert llm.kwargs["stream_usage"] is True
    assert llm.kwargs["max_tokens"] == 8192


def test_gonka_generation_defaults_do_not_override_callers(monkeypatch):
    _clear_gonka_env(monkeypatch)
    monkeypatch.setenv("GONKA_API_KEY", "router-key")
    monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)

    llm = mod.GonkaClient(
        "moonshotai/Kimi-K2.6",
        max_tokens=2048,
        streaming=False,
        stream_usage=False,
    ).get_llm()

    assert llm.kwargs["streaming"] is False
    assert llm.kwargs["stream_usage"] is False
    assert llm.kwargs["max_tokens"] == 2048


def test_sdk_llm_sets_same_kimi_safe_generation_defaults(monkeypatch):
    _clear_gonka_env(monkeypatch)
    monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)

    def fake_get(url: str, timeout: int):
        assert url == "https://node4.gonka.ai/v1/identity"
        assert timeout == 30

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": {"address": "transfer-address"}}

        return Response()

    fake_http_client = object()

    def fake_gonka_http_client(private_key: str, transfer_address: str):
        assert private_key == "private-key"
        assert transfer_address == "transfer-address"
        return fake_http_client

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setitem(
        sys.modules,
        "gonka_openai",
        SimpleNamespace(gonka_http_client=fake_gonka_http_client),
    )

    llm = mod.GonkaClient("moonshotai/Kimi-K2.6")._build_sdk_llm(
        "https://node4.gonka.ai",
        "private-key",
    )

    assert llm.kwargs["base_url"] == "https://node4.gonka.ai/v1"
    assert llm.kwargs["http_client"] is fake_http_client
    assert llm.kwargs["api_key"] == "gonka-network"
    assert llm.kwargs["streaming"] is True
    assert llm.kwargs["stream_usage"] is True
    assert llm.kwargs["max_tokens"] == 8192
