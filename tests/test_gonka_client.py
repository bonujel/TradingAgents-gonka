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


# ---------------------------------------------------------------------------
# Self-healing structured-output wrapper (see
# dev_notes/gonka-structured-output-resilience-design.md, Component 1)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def clear_broken_backends_cache():
    """Reset the process-level cache between tests in this class so each
    case starts from a clean state."""
    from tradingagents.llm_clients import gonka_client as gc
    gc._BACKENDS_WITHOUT_TOOL_CALLING.clear()
    yield
    gc._BACKENDS_WITHOUT_TOOL_CALLING.clear()


@pytest.mark.unit
class TestLearningStructuredRunnable:
    """The wrapper detects the vLLM 'auto tool choice' error on first
    invoke, switches to a json_mode runnable, and caches that decision
    for the process lifetime so subsequent calls skip the function_calling
    attempt entirely."""

    def test_module_state_exists(self):
        from tradingagents.llm_clients import gonka_client as gc
        assert isinstance(gc._BACKENDS_WITHOUT_TOOL_CALLING, set)
        assert "tool choice requires --enable-auto-tool-choice" in gc._VLLM_TOOL_CHOICE_ERROR_HINT

    def test_first_invoke_downgrades_on_vllm_tool_choice_error(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError(
            'Error: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
        )

        # Stand in for the host LLM. We only need the attributes the
        # wrapper reads (model_name, openai_api_base) and a downgrade
        # builder it can call.
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"
        downgraded = MagicMock()
        downgraded.invoke.return_value = "json_mode_result"

        wrapper = _LearningStructuredRunnable(
            primary=primary,
            host=host,
            schema=object,  # opaque; the downgrade builder is patched below
            extra_kwargs={},
        )
        # Patch the json_mode build so we do not require a real LLM.
        wrapper._build_json_mode = lambda: downgraded

        result = wrapper.invoke("prompt")
        assert result == "json_mode_result"
        # Primary was tried exactly once before the downgrade.
        primary.invoke.assert_called_once_with("prompt")
        # Downgrade was invoked exactly once with the same prompt.
        downgraded.invoke.assert_called_once_with("prompt")
        # Cache now reflects the broken backend.
        assert ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1") in _BACKENDS_WITHOUT_TOOL_CALLING

    def test_subsequent_invoke_skips_primary(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import _LearningStructuredRunnable

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError(
            'Error: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
        )
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"
        downgraded = MagicMock()
        downgraded.invoke.return_value = "json_mode_result"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: downgraded

        wrapper.invoke("prompt-1")
        wrapper.invoke("prompt-2")

        # Primary was attempted only on the first call.
        assert primary.invoke.call_count == 1
        # Downgrade handled both calls.
        assert downgraded.invoke.call_count == 2
        downgraded.invoke.assert_called_with("prompt-2")

    def test_unrelated_error_propagates_and_does_not_cache(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError("503 Service Unavailable")
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: (_ for _ in ()).throw(
            AssertionError("downgrade builder must not be called")
        )

        with pytest.raises(RuntimeError, match="503"):
            wrapper.invoke("prompt")
        assert ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1") not in _BACKENDS_WITHOUT_TOOL_CALLING

    def test_cache_clear_simulates_process_restart(self, clear_broken_backends_cache):
        """When the cache is empty (process just started) and the primary
        now succeeds (Gonka has been fixed), function_calling is used and
        the cache stays empty — no code change required on the client side."""
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.return_value = "function_calling_result"
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: (_ for _ in ()).throw(
            AssertionError("downgrade builder must not be called")
        )

        result = wrapper.invoke("prompt")
        assert result == "function_calling_result"
        # Cache stays empty — no broken-backend observation was made.
        assert len(_BACKENDS_WITHOUT_TOOL_CALLING) == 0
