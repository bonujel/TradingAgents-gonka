"""Tests for app.runner retryable-exception classification.

The runner whitelists transient failures via _is_retryable so that the
runner's 5s/15s/45s backoff engages only on known-recoverable cases.

The original StructuredOutputEmpty marker was removed when the three
decision-making agents flipped to a free-text-only path
(``v1-nfrontend-free-text``); the agents now produce prose directly and
``parse_rating`` recovers the rating heuristically — there is no more
"empty structured output" exception to retry.
"""

import logging

import pytest
from openai import APIError, BadRequestError

from app.runner import _count_node_retries, _is_retryable, _NodeRetryCounter


@pytest.mark.unit
class TestGenericValueErrorNotRetryable:
    def test_generic_value_error_without_marker_is_not_retryable(self):
        """Defensive: arbitrary ValueErrors must not be auto-retried. The
        whitelist is substring-based and only the ``No generations found in
        stream`` upstream-disconnect marker remains."""
        exc = ValueError("something else went wrong")
        assert _is_retryable(exc) is False

    def test_no_generations_marker_remains_retryable(self):
        """The one remaining ValueError marker — surface for an empty
        upstream stream — must stay retryable so the 5s/15s/45s backoff
        engages."""
        exc = ValueError("No generations found in stream")
        assert _is_retryable(exc) is True


@pytest.mark.unit
class TestJsonDecodeApiErrorIsRetryable:
    """A bare openai.APIError whose message is a JSON decode failure means
    the SDK could not parse a corrupted streamed SSE chunk — a transient
    upstream-proxy fault, safe to retry."""

    def test_expecting_delimiter_is_retryable(self):
        exc = APIError(
            "Expecting ',' delimiter: line 1 column 34 (char 33)",
            request=None,  # type: ignore[arg-type]
            body=None,
        )
        assert _is_retryable(exc) is True

    def test_other_json_decode_variants_are_retryable(self):
        for msg in (
            "Expecting value: line 1 column 1 (char 0)",
            "Unterminated string starting at: line 2 column 5 (char 99)",
            "Expecting property name enclosed in double quotes: "
            "line 1 column 2 (char 1)",
        ):
            exc = APIError(msg, request=None, body=None)  # type: ignore[arg-type]
            assert _is_retryable(exc) is True, msg

    def test_plain_api_error_without_json_signature_not_retryable(self):
        """An APIError that is neither a Gonka chain marker nor a JSON
        decode failure must still fail fast."""
        exc = APIError(
            "some unrecognised upstream error",
            request=None,  # type: ignore[arg-type]
            body=None,
        )
        assert _is_retryable(exc) is False

    def test_bad_request_subclass_not_retryable(self):
        """BadRequestError subclasses APIError but is a client-side fault;
        even with a JSON-ish message it must not be retried (the
        classifier uses a strict `type(exc) is APIError` check)."""
        exc = BadRequestError.__new__(BadRequestError)
        Exception.__init__(exc, "bad: line 1 column 2 (char 1)")
        assert _is_retryable(exc) is False


@pytest.mark.unit
class TestNodeRetryCounter:
    """run_one taps the langgraph retry logger so a persisted failure can
    honestly report node-level retries — the ones the ticker-level
    counter cannot see."""

    @staticmethod
    def _record(msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            "langgraph.pregel._retry", logging.INFO, __file__, 0, msg, None, None
        )

    def test_counts_only_langgraph_retry_records(self):
        counter = _NodeRetryCounter()
        # The exact prefix LangGraph emits on every node retry.
        counter.emit(self._record(
            "Retrying task Market Analyst after 2.00 seconds (attempt 2) after X"
        ))
        counter.emit(self._record(
            "Retrying task News Analyst after 6.00 seconds (attempt 3) after Y"
        ))
        counter.emit(self._record("Graph step 5 complete"))  # unrelated
        assert counter.count == 2

    def test_context_manager_attaches_and_detaches(self):
        lg = logging.getLogger("langgraph.pregel._retry")
        before = list(lg.handlers)
        with _count_node_retries() as counter:
            assert counter in lg.handlers
        assert lg.handlers == before  # handler removed on exit
