"""Tests for the LangGraph node-level retry policy.

A transient Gonka failure should re-run only the failing node, not the
whole ticker pipeline. The policy is built by
``tradingagents.graph.setup._node_retry_policy`` and shares its
exception classifier with app/runner.py's ticker-level backstop.
"""

import httpx
import pytest

from tradingagents.agents.utils.degeneracy import DegenerateOutputError
from tradingagents.graph.setup import _is_node_retryable, _node_retry_policy


@pytest.mark.unit
class TestNodeRetryPolicy:
    def test_policy_uses_node_predicate(self):
        policy = _node_retry_policy()
        assert policy.retry_on is _is_node_retryable

    def test_node_predicate_retries_transient_transport_errors(self):
        """The node predicate must cover everything the ticker-level
        backstop covers, so transport blips recover at the cheap
        granularity first."""
        assert _is_node_retryable(httpx.RemoteProtocolError("peer closed")) is True

    def test_node_predicate_retries_degenerate_output(self):
        """DegenerateOutputError is retryable at the node level — a
        re-roll usually lands on a healthy executor."""
        assert _is_node_retryable(DegenerateOutputError("salad")) is True

    def test_node_predicate_rejects_code_bugs(self):
        assert _is_node_retryable(KeyError("missing")) is False

    def test_degenerate_output_is_not_ticker_level_retryable(self):
        """The bounded-cost guarantee: DegenerateOutputError recovers at
        the node level only. The ticker-level classifier must reject it
        so a persistently bad backend is not also re-run pipeline-wide."""
        from tradingagents.llm_clients.retry import is_transient_llm_error

        assert is_transient_llm_error(DegenerateOutputError("salad")) is False

    def test_default_attempt_count(self):
        policy = _node_retry_policy()
        assert policy.max_attempts == 3
        # Backoff is bounded and jittered so a burst of node retries does
        # not synchronise across the thread pool.
        assert policy.jitter is True
        assert policy.max_interval <= 60

    def test_attempt_count_env_override(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_NODE_RETRY_ATTEMPTS", "5")
        assert _node_retry_policy().max_attempts == 5

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_NODE_RETRY_ATTEMPTS", "not-a-number")
        assert _node_retry_policy().max_attempts == 3

    def test_env_override_is_floored_at_one(self, monkeypatch):
        """0 or negative would disable retries entirely and is almost
        certainly a misconfiguration — clamp to at least one attempt."""
        monkeypatch.setenv("TRADINGAGENTS_NODE_RETRY_ATTEMPTS", "0")
        assert _node_retry_policy().max_attempts == 1


@pytest.mark.unit
class TestGraphNodesCarryRetryPolicy:
    def test_every_llm_and_tool_node_has_a_retry_policy(self):
        """Build the full graph and confirm the analyst, researcher,
        manager, trader, risk and tool nodes all carry a retry policy.
        Msg-clear nodes intentionally do not — they cannot fail
        transiently."""
        from unittest.mock import MagicMock

        from langgraph.prebuilt import ToolNode

        from tradingagents.graph.conditional_logic import ConditionalLogic
        from tradingagents.graph.setup import GraphSetup

        tools = {a: ToolNode([]) for a in ("market", "social", "news", "fundamentals")}
        setup = GraphSetup(MagicMock(), MagicMock(), tools, ConditionalLogic())
        workflow = setup.setup_graph(["market", "social", "news", "fundamentals"])

        for name, spec in workflow.nodes.items():
            if name.startswith("Msg Clear"):
                continue
            assert spec.retry_policy, f"node {name!r} has no retry policy"
