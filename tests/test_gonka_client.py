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
    """The wrapper detects a backend without working tool-calling on the
    first invoke — either a vLLM 'auto tool choice' 400 or a silent None
    return (model emitted no tool call) — switches to a json_mode
    runnable, and caches that decision for the process lifetime so
    subsequent calls skip the function_calling attempt entirely."""

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

    def test_first_invoke_downgrades_on_none_return(self, clear_broken_backends_cache):
        """A primary that returns None — the model answered in prose and
        emitted no tool call, so LangChain's parser yielded None — triggers
        the same json_mode downgrade as the vLLM 400. The prompt is retried
        on json_mode and the broken backend is cached."""
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.return_value = None  # no tool call -> parser yields None
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"
        downgraded = MagicMock()
        downgraded.invoke.return_value = "json_mode_result"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: downgraded

        result = wrapper.invoke("prompt")
        assert result == "json_mode_result"
        primary.invoke.assert_called_once_with("prompt")
        downgraded.invoke.assert_called_once_with("prompt")
        assert ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1") in _BACKENDS_WITHOUT_TOOL_CALLING

    def test_none_return_downgrade_is_cached_for_next_call(self, clear_broken_backends_cache):
        """After a None-triggered downgrade, the next call skips the primary
        entirely — same caching behaviour as the 400-triggered path."""
        from tradingagents.llm_clients.gonka_client import _LearningStructuredRunnable

        primary = MagicMock()
        primary.invoke.return_value = None
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

        assert primary.invoke.call_count == 1
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


@pytest.mark.unit
class TestGonkaWithStructuredOutputIntegration:
    """GonkaStreamSafeChatOpenAI.with_structured_output consults the
    cache (skipping function_calling for known-broken backends) and
    wraps every returned runnable so first-call discovery still works
    for backends not yet in the cache."""

    def test_returns_learning_wrapper(self, monkeypatch, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _LearningStructuredRunnable,
        )
        # The superclass with_structured_output is replaced with a stub
        # that returns a sentinel runnable — we only need to check that
        # the wrapper wraps it.
        sentinel_primary = MagicMock(name="primary_runnable")
        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            lambda self, schema, **kwargs: sentinel_primary,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        wrapped = host.with_structured_output(object)
        assert isinstance(wrapped, _LearningStructuredRunnable)
        assert wrapped._primary is sentinel_primary

    def test_cached_backend_uses_json_mode_method(self, monkeypatch, clear_broken_backends_cache):
        """When the cache says this backend is broken, with_structured_output
        delegates with method='json_mode' even if the caller did not specify
        a method — so the primary runnable is already a json_mode one and
        the wrapper never has to downgrade."""
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )
        captured_kwargs = {}

        def fake_super_with(self, schema, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(name="bound_runnable")

        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            fake_super_with,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        _BACKENDS_WITHOUT_TOOL_CALLING.add(
            ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1")
        )
        host.with_structured_output(object)
        assert captured_kwargs.get("method") == "json_mode"

    def test_explicit_method_kwarg_wins_over_cache(self, monkeypatch, clear_broken_backends_cache):
        """If a caller explicitly passes method=..., we respect it. The
        cache only fills in method when the caller left it unset."""
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )
        captured_kwargs = {}

        def fake_super_with(self, schema, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(name="bound_runnable")

        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            fake_super_with,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        _BACKENDS_WITHOUT_TOOL_CALLING.add(
            ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1")
        )
        host.with_structured_output(object, method="function_calling")
        assert captured_kwargs.get("method") == "function_calling"
