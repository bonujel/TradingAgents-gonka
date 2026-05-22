# TradingAgents/graph/setup.py

import os
from typing import Any, Dict
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.degeneracy import DegenerateOutputError
from tradingagents.llm_clients.retry import is_transient_llm_error

from .conditional_logic import ConditionalLogic


def _is_node_retryable(exc: Exception) -> bool:
    """Node-level retry predicate.

    Retries transient transport failures (``is_transient_llm_error``,
    shared with app/runner.py's ticker-level backstop) **plus**
    ``DegenerateOutputError``.

    The degenerate-output case is retried only here, not at the ticker
    level: re-running a single node on a fresh executor is cheap, whereas
    a persistently degenerate backend should fail fast after a few node
    re-rolls rather than also re-running the whole pipeline. Bounding the
    cost this way is why ``DegenerateOutputError`` is excluded from
    ``is_transient_llm_error``.
    """
    return is_transient_llm_error(exc) or isinstance(exc, DegenerateOutputError)


def _node_retry_policy() -> RetryPolicy:
    """RetryPolicy attached to every graph node.

    A transient Gonka/transport failure (peer-closed stream, 5xx, 429, a
    corrupted SSE chunk, ...) or a degenerate-output rejection re-runs
    only the failing node — every prior node's committed state is kept —
    instead of restarting the whole ticker pipeline. This is the cheap
    first line of defence; the ticker-level loop in app/runner.py stays
    as a coarse backstop for transport failures only.

    Defaults: 3 attempts total, ~2s → 6s backoff with jitter. The attempt
    count is tunable via ``TRADINGAGENTS_NODE_RETRY_ATTEMPTS`` and also
    caps how many times a degenerate node is re-rolled.
    """
    try:
        attempts = max(1, int(os.environ.get("TRADINGAGENTS_NODE_RETRY_ATTEMPTS", "3")))
    except ValueError:
        attempts = 3
    return RetryPolicy(
        retry_on=_is_node_retryable,
        max_attempts=attempts,
        initial_interval=2.0,
        backoff_factor=3.0,
        max_interval=30.0,
        jitter=True,
    )


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # Create analyst nodes
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        if "market" in selected_analysts:
            analyst_nodes["market"] = create_market_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["market"] = create_msg_delete()
            tool_nodes["market"] = self.tool_nodes["market"]

        if "social" in selected_analysts:
            # "social" selector key preserved for back-compat with existing
            # user configs; the underlying agent has been renamed to
            # sentiment_analyst (the old name advertised social-media data
            # the agent never had access to — see issue #557).
            analyst_nodes["social"] = create_sentiment_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["social"] = create_msg_delete()
            tool_nodes["social"] = self.tool_nodes["social"]

        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["news"] = create_msg_delete()
            tool_nodes["news"] = self.tool_nodes["news"]

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["fundamentals"] = create_msg_delete()
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Every node carries the same retry policy: a transient upstream
        # failure re-runs just that node, not the whole ticker pipeline.
        retry_policy = _node_retry_policy()

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(
                f"{analyst_type.capitalize()} Analyst", node, retry_policy=retry_policy
            )
            workflow.add_node(
                f"Msg Clear {analyst_type.capitalize()}", delete_nodes[analyst_type]
            )
            workflow.add_node(
                f"tools_{analyst_type}", tool_nodes[analyst_type],
                retry_policy=retry_policy,
            )

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node, retry_policy=retry_policy)
        workflow.add_node("Bear Researcher", bear_researcher_node, retry_policy=retry_policy)
        workflow.add_node("Research Manager", research_manager_node, retry_policy=retry_policy)
        workflow.add_node("Trader", trader_node, retry_policy=retry_policy)
        workflow.add_node("Aggressive Analyst", aggressive_analyst, retry_policy=retry_policy)
        workflow.add_node("Neutral Analyst", neutral_analyst, retry_policy=retry_policy)
        workflow.add_node("Conservative Analyst", conservative_analyst, retry_policy=retry_policy)
        workflow.add_node("Portfolio Manager", portfolio_manager_node, retry_policy=retry_policy)

        # Define edges
        # Start with the first analyst
        first_analyst = selected_analysts[0]
        workflow.add_edge(START, f"{first_analyst.capitalize()} Analyst")

        # Connect analysts in sequence
        for i, analyst_type in enumerate(selected_analysts):
            current_analyst = f"{analyst_type.capitalize()} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_clear = f"Msg Clear {analyst_type.capitalize()}"

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(selected_analysts) - 1:
                next_analyst = f"{selected_analysts[i+1].capitalize()} Analyst"
                workflow.add_edge(current_clear, next_analyst)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
