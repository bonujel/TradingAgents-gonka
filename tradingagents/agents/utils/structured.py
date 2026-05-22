"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

from tradingagents.agents.utils.degeneracy import check_not_degenerate

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# Threshold below which a rendered structured-output response is treated
# as a stream-truncation / empty-fallback artifact rather than a genuine
# short answer. Picked from kimi-run-23 evidence: the shortest legitimately
# useful final_trade_decision observed was ~320 chars; degenerate outputs
# were either empty or under ~240 chars and visibly truncated mid-sentence.
# 80 is well below the legitimate floor while still catching empty /
# near-empty strings unambiguously.
_MIN_RENDERED_CHARS = 80


class StructuredOutputEmpty(ValueError):
    """Raised when both the structured-output render and the free-text
    fallback produce fewer than ``min_chars`` non-whitespace characters.

    Subclasses ``ValueError`` (not ``RuntimeError``) so it lines up with
    app/runner.py's existing retryable-``ValueError``-marker pattern: the
    exception message MUST contain the substring "no usable content from
    structured output" so ``_RETRYABLE_VALUE_ERROR_MARKERS`` recognises
    it and engages the standard 5s -> 15s -> 45s backoff. See
    dev_notes/gonka-structured-output-resilience-design.md, Component 4.
    """


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    min_chars: int = _MIN_RENDERED_CHARS,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.

    Raises ``StructuredOutputEmpty`` if neither path produces at least
    ``min_chars`` characters of non-whitespace content. The exception
    is a ``ValueError`` subclass whose message contains the marker
    "no usable content from structured output" so the runner classifies
    it as retryable (see ``app/runner.py:_RETRYABLE_VALUE_ERROR_MARKERS``).
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            rendered = render(result)
            if len(rendered.strip()) >= min_chars:
                # A degenerate render raises DegenerateOutputError, caught
                # just below — so the free-text path gets a fresh attempt
                # (likely a different Gonka executor) before giving up.
                check_not_degenerate(rendered, agent_name)
                return rendered
            logger.warning(
                "%s: structured output rendered to %d chars (<%d); "
                "falling back to free text",
                agent_name, len(rendered.strip()), min_chars,
            )
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    content = response.content or ""
    if len(content.strip()) < min_chars:
        raise StructuredOutputEmpty(
            f"{agent_name}: no usable content from structured output "
            f"(got {len(content.strip())} chars, threshold {min_chars}). "
            f"Likely upstream stream truncation; runner will retry."
        )
    # Free-text degeneracy is not recoverable here — let it propagate so
    # the LangGraph node-level retry re-rolls on a fresh executor.
    check_not_degenerate(content, agent_name)
    return content
