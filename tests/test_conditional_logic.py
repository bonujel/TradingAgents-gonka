"""Tests for ``tradingagents.graph.conditional_logic.ConditionalLogic``.

The big change covered here (2026-05-25): every ``should_continue_<analyst>``
routes through a shared helper that caps the per-analyst tool-call rounds.
Before the cap, the four analyst loops were unbounded — verified on the
deploy host with NVDA on Qwen, the Market Analyst kept emitting
``tool_calls`` indefinitely until langgraph's ``recursion_limit=100``
fired with ``GRAPH_RECURSION_LIMIT``, killing the whole ticker.

The cap forces a graceful exit to ``Msg Clear <Analyst>`` after
``max_tool_call_rounds`` rounds even when the model still asked for
more tools, so the analyst's partial output is preserved in
``state["<analyst>_report"]`` and the rest of the ticker pipeline keeps
running.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.graph.conditional_logic import (
    ConditionalLogic,
    _DEFAULT_MAX_TOOL_CALL_ROUNDS,
    _tool_call_rounds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ai_with_tools(*tool_names: str) -> AIMessage:
    """Build an AIMessage carrying one tool_call per tool name. The exact
    payload doesn't matter for the routing logic — it only checks
    truthiness of ``tool_calls`` and counts the messages that have one."""
    tool_calls = [
        {"id": f"call-{i}", "name": name, "args": {}}
        for i, name in enumerate(tool_names)
    ]
    return AIMessage(content="", tool_calls=tool_calls)


def _ai_plain(text: str = "done") -> AIMessage:
    return AIMessage(content=text)


def _state(messages: list) -> dict:
    """Build the minimal AgentState shape used by the routing functions."""
    return {"messages": messages}


# ---------------------------------------------------------------------------
# _tool_call_rounds: the underlying counter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToolCallRounds:
    def test_empty_messages_is_zero(self):
        assert _tool_call_rounds([]) == 0

    def test_plain_messages_do_not_count(self):
        msgs = [HumanMessage(content="hi"), _ai_plain("ok")]
        assert _tool_call_rounds(msgs) == 0

    def test_ai_with_tool_calls_counts(self):
        msgs = [HumanMessage(content="q"), _ai_with_tools("get_stock_data")]
        assert _tool_call_rounds(msgs) == 1

    def test_each_ai_with_tools_is_one_round_regardless_of_how_many_tools(self):
        """An AIMessage that calls 5 tools in one response is still ONE
        round — the analyst made one decision to invoke tools."""
        msgs = [
            HumanMessage(content="q"),
            _ai_with_tools("a", "b", "c", "d", "e"),
        ]
        assert _tool_call_rounds(msgs) == 1

    def test_multiple_rounds_accumulate(self):
        msgs = [
            HumanMessage(content="q"),
            _ai_with_tools("a"),
            HumanMessage(content="tool result"),
            _ai_with_tools("b"),
            HumanMessage(content="tool result"),
            _ai_with_tools("c"),
        ]
        assert _tool_call_rounds(msgs) == 3

    def test_non_ai_messages_with_truthy_attr_do_not_count(self):
        """Defensive: only ``AIMessage`` instances count. A duck-typed
        object that happens to have ``tool_calls`` does not."""
        impostor = SimpleNamespace(tool_calls=[{"name": "x"}])
        assert _tool_call_rounds([impostor]) == 0


# ---------------------------------------------------------------------------
# Routing: the four should_continue_<analyst> functions, parametrised.
# ---------------------------------------------------------------------------

ANALYST_CASES = [
    pytest.param("should_continue_market",       "tools_market",       "Msg Clear Market",       id="market"),
    pytest.param("should_continue_social",       "tools_social",       "Msg Clear Social",       id="social"),
    pytest.param("should_continue_news",         "tools_news",         "Msg Clear News",         id="news"),
    pytest.param("should_continue_fundamentals", "tools_fundamentals", "Msg Clear Fundamentals", id="fundamentals"),
]


@pytest.mark.unit
class TestAnalystRouting:
    @pytest.mark.parametrize("method, tools_node, clear_node", ANALYST_CASES)
    def test_last_message_has_tool_calls_under_cap_routes_to_tools(
        self, method, tools_node, clear_node,
    ):
        """Normal in-loop case: LLM asked for tools, we're still under
        the cap → continue the tool-call loop."""
        cl = ConditionalLogic(max_tool_call_rounds=8)
        state = _state([HumanMessage(content="analyse"), _ai_with_tools("any")])
        assert getattr(cl, method)(state) == tools_node

    @pytest.mark.parametrize("method, tools_node, clear_node", ANALYST_CASES)
    def test_last_message_has_no_tool_calls_exits_loop(
        self, method, tools_node, clear_node,
    ):
        """Natural exit: LLM stopped asking for tools → leave loop."""
        cl = ConditionalLogic(max_tool_call_rounds=8)
        state = _state([
            HumanMessage(content="analyse"),
            _ai_with_tools("first_tool"),
            HumanMessage(content="tool result"),
            _ai_plain("here is my final report ..."),  # no tool_calls
        ])
        assert getattr(cl, method)(state) == clear_node

    @pytest.mark.parametrize("method, tools_node, clear_node", ANALYST_CASES)
    def test_cap_forces_exit_on_round_after_max(
        self, method, tools_node, clear_node,
    ):
        """The core fix: once the model has issued more than
        ``max_tool_call_rounds`` tool-call rounds, route to clear
        regardless of whether the latest message still wants tools.
        This is what stops the NVDA-on-Qwen runaway pattern that hit
        ``GRAPH_RECURSION_LIMIT`` in run 2026-05-25 14:43.

        cap = 8, so 8 prior rounds + a 9th request → 9 > 8 → clear."""
        cl = ConditionalLogic(max_tool_call_rounds=8)
        messages = [HumanMessage(content="analyse")]
        for _ in range(8):  # 8 prior tool-call rounds already done
            messages.append(_ai_with_tools("anything"))
            messages.append(HumanMessage(content="tool result"))
        messages.append(_ai_with_tools("again_please"))  # 9th request — model still wants more
        assert getattr(cl, method)(_state(messages)) == clear_node

    @pytest.mark.parametrize("method, tools_node, clear_node", ANALYST_CASES)
    def test_exactly_at_cap_still_continues(
        self, method, tools_node, clear_node,
    ):
        """Boundary: with cap=8, the 8th request is the last allowed.
        7 prior rounds + this current 8th = 8 total, count == cap, not
        strictly greater, so we let it through. The 9th request is
        where the cap fires (covered by the test above)."""
        cl = ConditionalLogic(max_tool_call_rounds=8)
        messages = [HumanMessage(content="analyse")]
        for _ in range(7):
            messages.append(_ai_with_tools("any"))
            messages.append(HumanMessage(content="tool result"))
        messages.append(_ai_with_tools("8th_request"))
        assert getattr(cl, method)(_state(messages)) == tools_node


@pytest.mark.unit
class TestCapConfiguration:
    def test_default_cap_is_eight(self):
        """If the user does not override and no env var is set, the
        default is 8 — generous for healthy analyst flows (typical is
        2-4 rounds) while still well under langgraph's
        recursion_limit=100 even when all four analysts max out."""
        assert _DEFAULT_MAX_TOOL_CALL_ROUNDS == 8
        cl = ConditionalLogic()
        assert cl.max_tool_call_rounds == 8

    def test_constructor_override_takes_precedence(self):
        cl = ConditionalLogic(max_tool_call_rounds=3)
        messages = [HumanMessage(content="q")]
        for _ in range(3):
            messages.append(_ai_with_tools("any"))
            messages.append(HumanMessage(content="tool result"))
        messages.append(_ai_with_tools("more"))
        # 3 prior rounds, asking for 4th — cap fires.
        assert cl.should_continue_market(_state(messages)) == "Msg Clear Market"

    def test_env_var_changes_module_default_on_reimport(self, monkeypatch):
        """The module-level default reads ``TRADINGAGENTS_MAX_TOOL_CALL_ROUNDS``
        at import time. We verify by reloading the module."""
        import importlib

        from tradingagents.graph import conditional_logic as cl_mod

        monkeypatch.setenv("TRADINGAGENTS_MAX_TOOL_CALL_ROUNDS", "5")
        reloaded = importlib.reload(cl_mod)
        try:
            assert reloaded._DEFAULT_MAX_TOOL_CALL_ROUNDS == 5
            assert reloaded.ConditionalLogic().max_tool_call_rounds == 5
        finally:
            # Restore original module state so later tests aren't affected.
            monkeypatch.delenv("TRADINGAGENTS_MAX_TOOL_CALL_ROUNDS", raising=False)
            importlib.reload(cl_mod)


@pytest.mark.unit
class TestDebateRoutingUntouched:
    """Sanity: the debate and risk-analysis routing functions, which
    were not part of this fix, still behave as before."""

    def test_debate_routes_to_research_manager_after_max_rounds(self):
        cl = ConditionalLogic(max_debate_rounds=1)
        state = {
            "investment_debate_state": {"count": 2, "current_response": "Bull says ..."},
        }
        assert cl.should_continue_debate(state) == "Research Manager"

    def test_risk_routes_to_pm_after_max_rounds(self):
        cl = ConditionalLogic(max_risk_discuss_rounds=1)
        state = {
            "risk_debate_state": {"count": 3, "latest_speaker": "Aggressive Analyst"},
        }
        assert cl.should_continue_risk_analysis(state) == "Portfolio Manager"
