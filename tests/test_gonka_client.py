"""Unit tests for Gonka-specific LLM construction defaults."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest

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
# Free-text-only refactor: the structured-output (json_schema) machinery
# was removed in v1-nfrontend-free-text because Gonka's vLLM caps
# json_schema completions at ~3072 tokens, which made every Kimi-K2.6
# structured attempt fail length-limit (see run 33 log analysis). The
# three decision-making agents now emit free-text prose directly; the
# rating is recovered via tradingagents.agents.utils.rating.parse_rating.
# These assertions guard against regression.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStructuredOutputMachineryRemoved:
    def test_helpers_deleted(self):
        from tradingagents.llm_clients import gonka_client as gc

        for name in (
            "_inline_schema_refs",
            "_json_schema_response_format",
            "_GonkaJsonSchemaRunnable",
            # ...and the older downgrade machinery that the json_schema
            # refactor (方案 X) had already removed.
            "_BACKENDS_WITHOUT_TOOL_CALLING",
            "_LearningStructuredRunnable",
            "_VLLM_TOOL_CHOICE_ERROR_HINT",
        ):
            assert not hasattr(gc, name), f"{name} should have been deleted"

    def test_gonka_class_does_not_override_with_structured_output(self):
        """No Gonka-specific override remains. Any future caller that
        needs structured output falls through to the langchain-openai
        default behaviour rather than a half-broken json_schema path."""
        from tradingagents.llm_clients.gonka_client import GonkaStreamSafeChatOpenAI
        from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

        # The method exists on the base ChatOpenAI hierarchy; the test is
        # specifically that GonkaStreamSafeChatOpenAI does not define its
        # own override.
        assert "with_structured_output" not in GonkaStreamSafeChatOpenAI.__dict__
        # Sanity: still resolvable via inheritance from langchain-openai.
        assert hasattr(NormalizedChatOpenAI, "with_structured_output")

    def test_schemas_module_deleted(self):
        with pytest.raises(ModuleNotFoundError):
            import tradingagents.agents.schemas  # noqa: F401

    def test_structured_helper_module_deleted(self):
        with pytest.raises(ModuleNotFoundError):
            import tradingagents.agents.utils.structured  # noqa: F401


# ---------------------------------------------------------------------------
# Kimi thinking toggle. Gonka's vLLM honours
# ``chat_template_kwargs.thinking=False`` for Kimi-K2.6 (verified
# 2026-05-25: reasoning_len 6572 → 0, completion_tokens 1891 → ~450,
# content_len actually increases). The Settings UI flips this on demand via
# TRADINGAGENTS_DISABLE_KIMI_THINKING; the client reads the env per
# get_llm() so the toggle takes effect on the next ticker without a
# process restart.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKimiThinkingToggle:
    def _build_kwargs(self, monkeypatch, model: str, **extra_kwargs) -> dict:
        _clear_gonka_env(monkeypatch)
        monkeypatch.setenv("GONKA_API_KEY", "router-key")
        monkeypatch.setattr(mod, "GonkaStreamSafeChatOpenAI", _FakeChatOpenAI)
        return mod.GonkaClient(model, **extra_kwargs).get_llm().kwargs

    def test_default_no_extra_body_for_kimi(self, monkeypatch):
        # Default: env unset → thinking left on → no extra_body injected.
        monkeypatch.delenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", raising=False)
        kwargs = self._build_kwargs(monkeypatch, "moonshotai/Kimi-K2.6")
        # If model_kwargs is set at all, it must not have rewritten extra_body.
        mk = kwargs.get("model_kwargs", {})
        extra = mk.get("extra_body", {})
        assert "chat_template_kwargs" not in extra

    def test_env_truthy_injects_thinking_false_for_kimi(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", "1")
        kwargs = self._build_kwargs(monkeypatch, "moonshotai/Kimi-K2.6")
        extra = kwargs["model_kwargs"]["extra_body"]
        assert extra["chat_template_kwargs"] == {"thinking": False}

    def test_env_truthy_does_not_affect_non_kimi(self, monkeypatch):
        """Scope is Kimi-only. Qwen has its own degeneracy issues that have
        nothing to do with reasoning_content and must not see the toggle."""
        monkeypatch.setenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", "1")
        kwargs = self._build_kwargs(
            monkeypatch, "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
        )
        mk = kwargs.get("model_kwargs", {})
        extra = mk.get("extra_body", {})
        assert "chat_template_kwargs" not in extra

    def test_env_falsy_does_not_inject(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", "0")
        kwargs = self._build_kwargs(monkeypatch, "moonshotai/Kimi-K2.6")
        mk = kwargs.get("model_kwargs", {})
        extra = mk.get("extra_body", {})
        assert "chat_template_kwargs" not in extra

    def test_user_supplied_extra_body_is_merged_not_clobbered(self, monkeypatch):
        """Caller passes their own extra_body for something unrelated — the
        toggle injection must preserve it alongside the chat_template_kwargs
        addition."""
        monkeypatch.setenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", "1")
        kwargs = self._build_kwargs(
            monkeypatch,
            "moonshotai/Kimi-K2.6",
            model_kwargs={"extra_body": {"top_logprobs": 5}},
        )
        extra = kwargs["model_kwargs"]["extra_body"]
        # Both keys present, neither rewritten.
        assert extra["top_logprobs"] == 5
        assert extra["chat_template_kwargs"] == {"thinking": False}

