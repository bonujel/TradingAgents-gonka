"""Tests for the LangGraph node-level retry policy.

A transient Gonka failure should re-run only the failing node, not the
whole ticker pipeline. The policy is built by
``tradingagents.graph.setup._node_retry_policy`` and shares its
exception classifier with app/runner.py's ticker-level backstop.
"""

import pytest

from tradingagents.graph.setup import _node_retry_policy
from tradingagents.llm_clients.retry import is_transient_llm_error


@pytest.mark.unit
class TestNodeRetryPolicy:
    def test_policy_uses_shared_classifier(self):
        """retry_on must be the same predicate the runner uses, so the
        node-level and ticker-level layers never disagree."""
        policy = _node_retry_policy()
        assert policy.retry_on is is_transient_llm_error

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
