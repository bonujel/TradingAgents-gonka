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
# json_schema structured output (方案 X). function_calling, json_mode and the
# self-healing downgrade machinery (_LearningStructuredRunnable, the
# (model, base_url) cache) were removed — json_schema is the sole path.
# ---------------------------------------------------------------------------

import json
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.schemas import (
    PortfolioDecision,
    ResearchPlan,
    TraderProposal,
)


@pytest.mark.unit
class TestInlineSchemaRefs:
    """Gonka's gateway rejects $defs/$ref; the schema must be flattened
    self-contained before it is sent as a json_schema response_format."""

    def test_inlines_ref_and_drops_defs(self):
        from tradingagents.llm_clients.gonka_client import _inline_schema_refs

        raw = {
            "$defs": {"Color": {"type": "string", "enum": ["R", "B"]}},
            "type": "object",
            "properties": {"c": {"$ref": "#/$defs/Color"}},
            "required": ["c"],
        }
        out = _inline_schema_refs(raw)
        assert "$defs" not in out
        assert out["properties"]["c"] == {"type": "string", "enum": ["R", "B"]}
        assert "$ref" not in json.dumps(out)

    def test_sibling_keys_next_to_ref_are_kept(self):
        from tradingagents.llm_clients.gonka_client import _inline_schema_refs

        raw = {
            "$defs": {"E": {"type": "string", "enum": ["x"]}},
            "type": "object",
            "properties": {"f": {"$ref": "#/$defs/E", "description": "a field"}},
        }
        out = _inline_schema_refs(raw)
        assert out["properties"]["f"]["description"] == "a field"
        assert out["properties"]["f"]["enum"] == ["x"]

    def test_real_agent_schemas_flatten_without_refs(self):
        from tradingagents.llm_clients.gonka_client import _json_schema_response_format

        for schema in (ResearchPlan, TraderProposal, PortfolioDecision):
            rf = _json_schema_response_format(schema)
            assert rf["type"] == "json_schema"
            assert rf["json_schema"]["name"] == schema.__name__
            assert rf["json_schema"]["strict"] is True
            blob = json.dumps(rf)
            assert "$defs" not in blob
            assert "$ref" not in blob


@pytest.mark.unit
class TestGonkaJsonSchemaRunnable:
    """with_structured_output returns a stateless json_schema runnable that
    parses the model's JSON completion into the Pydantic schema."""

    def test_with_structured_output_returns_json_schema_runnable(self):
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _GonkaJsonSchemaRunnable,
        )

        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        runnable = host.with_structured_output(ResearchPlan)
        assert isinstance(runnable, _GonkaJsonSchemaRunnable)

    def test_invoke_parses_completion_into_pydantic(self):
        from tradingagents.llm_clients.gonka_client import _GonkaJsonSchemaRunnable

        host = MagicMock()
        message = MagicMock()
        message.content = (
            '{"recommendation": "Buy", "rationale": "Strong services growth",'
            ' "strategic_actions": "Scale in on weakness"}'
        )
        host.bind.return_value.invoke.return_value = message

        runnable = _GonkaJsonSchemaRunnable(host, ResearchPlan)
        result = runnable.invoke("prompt")

        assert isinstance(result, ResearchPlan)
        assert result.recommendation.value == "Buy"
        # The flattened json_schema response_format reached the model.
        rf = host.bind.call_args.kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert "$defs" not in json.dumps(rf)

    def test_invoke_raises_on_truncated_json(self):
        """A truncated completion (the guided-decoding whitespace trap)
        surfaces as a ValidationError for the caller to catch + retry."""
        from pydantic import ValidationError

        from tradingagents.llm_clients.gonka_client import _GonkaJsonSchemaRunnable

        host = MagicMock()
        message = MagicMock()
        message.content = '{"recommendation": "Buy", "rationale": "abc'  # truncated
        host.bind.return_value.invoke.return_value = message

        runnable = _GonkaJsonSchemaRunnable(host, ResearchPlan)
        with pytest.raises(ValidationError):
            runnable.invoke("prompt")

    def test_invoke_raises_on_empty_content(self):
        from pydantic import ValidationError

        from tradingagents.llm_clients.gonka_client import _GonkaJsonSchemaRunnable

        host = MagicMock()
        message = MagicMock()
        message.content = ""
        host.bind.return_value.invoke.return_value = message

        runnable = _GonkaJsonSchemaRunnable(host, ResearchPlan)
        with pytest.raises(ValidationError):
            runnable.invoke("prompt")

    def test_no_process_level_downgrade_state(self):
        """方案 X removed the process-level (model, base_url) downgrade
        cache and the self-healing wrapper — confirm they are gone."""
        from tradingagents.llm_clients import gonka_client as gc

        assert not hasattr(gc, "_BACKENDS_WITHOUT_TOOL_CALLING")
        assert not hasattr(gc, "_LearningStructuredRunnable")
        assert not hasattr(gc, "_VLLM_TOOL_CHOICE_ERROR_HINT")
