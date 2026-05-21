"""Tests for app.runner retryable-exception classification.

The runner whitelists transient failures via _is_retryable so that the
runner's 5s/15s/45s backoff engages only on known-recoverable cases.
This file exercises the StructuredOutputEmpty marker contract added in
dev_notes/gonka-structured-output-resilience-design.md (Component 4).
"""

import pytest

from app.runner import _is_retryable
from tradingagents.agents.utils.structured import StructuredOutputEmpty


@pytest.mark.unit
class TestStructuredOutputEmptyIsRetryable:
    def test_marker_message_is_classified_retryable(self):
        """StructuredOutputEmpty carrying the standard marker substring
        must be retryable so the existing backoff salvages stream-
        truncation-induced empty responses."""
        exc = StructuredOutputEmpty(
            "Trader: no usable content from structured output (got 0 chars)"
        )
        assert _is_retryable(exc) is True

    def test_generic_value_error_without_marker_is_not_retryable(self):
        """Defensive: arbitrary ValueErrors must not be auto-retried, even
        though StructuredOutputEmpty inherits from ValueError. The
        whitelist is substring-based, not type-based."""
        exc = ValueError("something else went wrong")
        assert _is_retryable(exc) is False
