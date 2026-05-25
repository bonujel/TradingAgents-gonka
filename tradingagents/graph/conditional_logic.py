# TradingAgents/graph/conditional_logic.py

import os

from langchain_core.messages import AIMessage

from tradingagents.agents.utils.agent_states import AgentState


# Per-analyst hard cap on consecutive tool-call rounds. Without this cap
# the conditional ``last_message.tool_calls → tools_<analyst>`` edge can
# loop indefinitely whenever the LLM keeps emitting tool calls; only
# langgraph's recursion_limit (default 100) ultimately stops it, and at
# that point the whole ticker fails as
# ``GRAPH_RECURSION_LIMIT``. Verified on 2026-05-25 run, NVDA on Qwen
# fell into exactly this loop and burned ~45 HTTP calls in 72s before
# the recursion ceiling fired.
#
# 8 is comfortably above any healthy analyst (normal is 2-4 tool calls
# end-to-end). When the cap fires the analyst is force-routed to its
# Msg Clear node — the partial report it produced so far is preserved
# in ``state["<analyst>_report"]`` because each analyst writes its
# report back at every turn, not just at the natural exit.
_DEFAULT_MAX_TOOL_CALL_ROUNDS = int(
    os.environ.get("TRADINGAGENTS_MAX_TOOL_CALL_ROUNDS", "8")
)


def _tool_call_rounds(messages) -> int:
    """Count AIMessages that asked for a tool call in this analyst's
    current state["messages"]. Each such message is one round of
    ``analyst → tools_<analyst> → analyst``. The Msg Clear nodes wipe
    ``state["messages"]`` between analysts (via ``RemoveMessage`` —
    see ``tradingagents.agents.utils.agent_utils.create_msg_delete``),
    so this counter is naturally per-analyst, not cumulative."""
    return sum(
        1 for m in messages
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
    )


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(
        self,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        max_tool_call_rounds: int = _DEFAULT_MAX_TOOL_CALL_ROUNDS,
    ):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.max_tool_call_rounds = max_tool_call_rounds

    def _route_analyst(
        self,
        state: AgentState,
        tools_node: str,
        clear_node: str,
    ) -> str:
        """Shared routing for the four analyst-tool subgraphs.

        Routes to ``tools_node`` (continue the tool-call loop) only when
        the last message actually requested tools AND the per-analyst
        tool-call round count is still under the cap. Otherwise routes
        to ``clear_node`` which discards the in-flight messages and
        hands off to the next analyst. The cap prevents
        ``GRAPH_RECURSION_LIMIT`` from killing the whole ticker when one
        analyst's LLM cannot decide to stop calling tools.
        """
        messages = state["messages"]
        last_message = messages[-1]
        if not last_message.tool_calls:
            return clear_node
        # ``max_tool_call_rounds`` reads as "up to N rounds allowed", so
        # the cap fires only once the count strictly exceeds N. At
        # count == N we let the Nth round execute and only block the
        # (N+1)-th request.
        if _tool_call_rounds(messages) > self.max_tool_call_rounds:
            return clear_node
        return tools_node

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        return self._route_analyst(state, "tools_market", "Msg Clear Market")

    def should_continue_social(self, state: AgentState):
        """Determine if social media analysis should continue."""
        return self._route_analyst(state, "tools_social", "Msg Clear Social")

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        return self._route_analyst(state, "tools_news", "Msg Clear News")

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        return self._route_analyst(state, "tools_fundamentals", "Msg Clear Fundamentals")

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
