# Gonka structured-output resilience — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Trader / Research Manager / Portfolio Manager survive Gonka's currently-broken vLLM tool-calling backend, and never silently write empty `final_trade_decision`. Self-heal automatically when upstream is fixed — no client-side code or config change required.

**Architecture:** Two decoupled components. (1) `GonkaStreamSafeChatOpenAI.with_structured_output` learns at runtime whether the current backend rejects tools-bearing requests with the specific vLLM error and downgrades to `json_mode` for the process lifetime; next process start re-probes function_calling. (2) `invoke_structured_or_freetext` raises `StructuredOutputEmpty` (a `ValueError` subclass carrying a runner-visible marker substring) when both paths produce < 80 chars of usable content, so the existing runner backoff handles it as retryable. `capabilities.py` stays untouched — Kimi is not added there.

**Tech Stack:** Python 3.11, LangChain (`langchain-openai`), pytest with `pytest.mark.unit`, `unittest.mock.MagicMock` for LLM stubs.

**Reference spec:** `dev_notes/gonka-structured-output-resilience-design.md`

---

## File map

| Action | File | Responsibility |
|---|---|---|
| Modify | `tradingagents/agents/utils/structured.py` | Add `StructuredOutputEmpty` + `_MIN_RENDERED_CHARS`; enforce threshold in `invoke_structured_or_freetext` |
| Modify | `app/runner.py:69` | Add one entry to `_RETRYABLE_VALUE_ERROR_MARKERS` |
| Modify | `tradingagents/llm_clients/gonka_client.py` | Add `_BACKENDS_WITHOUT_TOOL_CALLING`, `_VLLM_TOOL_CHOICE_ERROR_HINT`, `_LearningStructuredRunnable`; override `GonkaStreamSafeChatOpenAI.with_structured_output` |
| Modify | `tests/test_structured_agents.py` | Add `TestStructuredHelperEmptyContent` class (5 cases) |
| Modify | `tests/test_gonka_client.py` | Add `TestStructuredOutputAutoDowngrade` class (4 cases) |
| Create | `tests/test_runner_retry.py` | One test asserting the new marker is classified retryable by `_is_retryable` |
| Modify | `tests/test_memory_log.py:723` | Extend `plain_response` to ≥ 80 chars so the PM fallback test stays valid under the new threshold |
| Untouched | `tradingagents/llm_clients/capabilities.py` | No change — table stays model-protocol-only |
| Untouched | `tradingagents/llm_clients/openai_client.py` | No change |

---

## Task 1: Add `StructuredOutputEmpty` exception + `_MIN_RENDERED_CHARS` constant

**Files:**
- Modify: `tradingagents/agents/utils/structured.py`
- Test: `tests/test_structured_agents.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_structured_agents.py`. After the existing `TestResearchManagerAgent` class (around line 233), append:

```python
# ---------------------------------------------------------------------------
# Structured helper: empty / short content handling (resilience against
# upstream stream truncation; see dev_notes/kimi-run-23-2026-05-20.md and
# dev_notes/gonka-structured-output-resilience-design.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStructuredHelperEmptyContent:
    """Empty / near-empty fallback content must raise so the runner can retry,
    not be silently written into trader_investment_plan / investment_plan /
    final_trade_decision (cause of run 23 ok_empty_final records)."""

    def test_empty_exception_is_value_error_with_runner_marker(self):
        """Contract with app/runner.py _RETRYABLE_VALUE_ERROR_MARKERS.

        The runner whitelists retryable transient failures by exception
        type + substring; StructuredOutputEmpty must satisfy both so the
        existing 5s/15s/45s backoff engages.
        """
        from tradingagents.agents.utils.structured import StructuredOutputEmpty
        exc = StructuredOutputEmpty(
            "Trader: no usable content from structured output (got 0 chars)"
        )
        assert isinstance(exc, ValueError)
        assert "no usable content from structured output" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_structured_agents.py::TestStructuredHelperEmptyContent::test_empty_exception_is_value_error_with_runner_marker -v`

Expected: FAIL with `ImportError: cannot import name 'StructuredOutputEmpty'`

- [ ] **Step 3: Add the exception and threshold constant**

Edit `tradingagents/agents/utils/structured.py`. After the existing module docstring and imports (after line 28 `T = TypeVar("T", bound=BaseModel)`), insert:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_structured_agents.py::TestStructuredHelperEmptyContent::test_empty_exception_is_value_error_with_runner_marker -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/utils/structured.py tests/test_structured_agents.py
git commit -m "feat(gonka): StructuredOutputEmpty exception + 80-char threshold

Adds the exception type used by invoke_structured_or_freetext to fail
empty / truncated structured-output responses (subsequent commit). Subclass
of ValueError + 'no usable content from structured output' marker make it
retryable via app/runner.py's existing _RETRYABLE_VALUE_ERROR_MARKERS path."
```

---

## Task 2: Enforce threshold in `invoke_structured_or_freetext`

**Files:**
- Modify: `tradingagents/agents/utils/structured.py:48-73`
- Test: `tests/test_structured_agents.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_structured_agents.py`, append four more cases to `TestStructuredHelperEmptyContent`:

```python
    def test_empty_freetext_fallback_raises(self):
        from tradingagents.agents.utils.structured import StructuredOutputEmpty
        llm = MagicMock()
        # Force the free-text branch by making structured-output unsupported.
        llm.with_structured_output.side_effect = NotImplementedError("unsupported")
        llm.invoke.return_value = MagicMock(content="")
        trader = create_trader(llm)
        with pytest.raises(StructuredOutputEmpty) as exc_info:
            trader(_make_trader_state())
        # Marker present so the runner classifies as retryable.
        assert "no usable content from structured output" in str(exc_info.value)

    def test_whitespace_only_fallback_raises(self):
        from tradingagents.agents.utils.structured import StructuredOutputEmpty
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("unsupported")
        llm.invoke.return_value = MagicMock(content="   \n\t  ")
        trader = create_trader(llm)
        with pytest.raises(StructuredOutputEmpty):
            trader(_make_trader_state())

    def test_short_structured_render_falls_through_to_freetext(self):
        """Structured returns a valid schema but the render is sub-threshold;
        free-text returns enough content. Free-text result wins."""
        captured = {}
        # A minimal TraderProposal renders to ~70 chars (action + reasoning).
        short_proposal = TraderProposal(action=TraderAction.HOLD, reasoning="x.")
        structured = MagicMock()
        structured.invoke.side_effect = lambda prompt: (
            captured.__setitem__("prompt", prompt) or short_proposal
        )
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        long_plain = (
            "**Action**: Hold\n\n"
            + ("Detailed reasoning across multiple factors. " * 5)
            + "\n\nFINAL TRANSACTION PROPOSAL: **HOLD**"
        )
        llm.invoke.return_value = MagicMock(content=long_plain)
        trader = create_trader(llm)
        result = trader(_make_trader_state())
        assert result["trader_investment_plan"] == long_plain

    def test_rm_empty_fallback_raises(self):
        """Research Manager shares the same helper — coverage proves the
        helper change applies uniformly, not just to Trader."""
        from tradingagents.agents.utils.structured import StructuredOutputEmpty
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("unsupported")
        llm.invoke.return_value = MagicMock(content="")
        rm = create_research_manager(llm)
        with pytest.raises(StructuredOutputEmpty):
            rm(_make_rm_state())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_structured_agents.py::TestStructuredHelperEmptyContent -v`

Expected: the four new tests FAIL (the existing marker test still passes). The empty-fallback tests fail because `invoke_structured_or_freetext` currently returns `""`. The short-render test fails because the helper currently returns the short render instead of falling through.

- [ ] **Step 3: Implement the threshold enforcement**

Edit `tradingagents/agents/utils/structured.py:48-73`. Replace the existing `invoke_structured_or_freetext` body with:

```python
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
    return content
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_structured_agents.py::TestStructuredHelperEmptyContent -v`

Expected: all 5 cases PASS.

Run the full `tests/test_structured_agents.py` to confirm no regression in the existing structured / fallback tests:

Run: `pytest tests/test_structured_agents.py -v`

Expected: all PASS.

- [ ] **Step 5: Verify the short-render-falls-through assertion is meaningful**

This step protects against a silent test bug: if `render(short_proposal)` happens to be ≥ 80 chars, the test would pass for the wrong reason. Confirm:

Run: `python -c "from tradingagents.agents.schemas import TraderProposal, TraderAction, render_trader_proposal; p = TraderProposal(action=TraderAction.HOLD, reasoning='x.'); r = render_trader_proposal(p); print(len(r.strip()), repr(r[:120]))"`

Expected: a length under 80. If the printed length is ≥ 80, shorten the reasoning further (e.g. `reasoning="."`) until it is under 80, and re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/agents/utils/structured.py tests/test_structured_agents.py
git commit -m "fix(gonka): fail invoke_structured_or_freetext on empty/short content

Empty or trivially short responses from the structured helper used to be
written into final_trade_decision / trader_investment_plan / investment_plan
as successful results (54 ok_empty_final records in Kimi run 23). Now both
the structured-render path and the free-text fallback enforce an 80-char
minimum and raise StructuredOutputEmpty on failure, with a marker substring
that the runner recognises as retryable."
```

---

## Task 3: Add runner retryable marker

**Files:**
- Modify: `app/runner.py:69-71`
- Create: `tests/test_runner_retry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runner_retry.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_runner_retry.py -v`

Expected: `test_marker_message_is_classified_retryable` FAILS (marker not yet in `_RETRYABLE_VALUE_ERROR_MARKERS`); `test_generic_value_error_without_marker_is_not_retryable` PASSES.

- [ ] **Step 3: Add the marker to the runner whitelist**

Edit `app/runner.py:69-71`. Replace:

```python
_RETRYABLE_VALUE_ERROR_MARKERS: tuple[str, ...] = (
    "No generations found in stream",
)
```

with:

```python
_RETRYABLE_VALUE_ERROR_MARKERS: tuple[str, ...] = (
    "No generations found in stream",
    # StructuredOutputEmpty from tradingagents/agents/utils/structured.py.
    # Raised when Trader / RM / PM produced no usable content (typically
    # caused by upstream stream truncation), so the existing 5s/15s/45s
    # backoff applies. See dev_notes/gonka-structured-output-resilience-design.md.
    "no usable content from structured output",
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_runner_retry.py -v`

Expected: both cases PASS.

- [ ] **Step 5: Commit**

```bash
git add app/runner.py tests/test_runner_retry.py
git commit -m "feat(runner): classify StructuredOutputEmpty as retryable

Extends _RETRYABLE_VALUE_ERROR_MARKERS with the marker raised by
tradingagents.agents.utils.structured.StructuredOutputEmpty. Empty /
truncated responses now flow into the existing 5s/15s/45s backoff
instead of being recorded as hard failures on first attempt."
```

---

## Task 4: Fix the existing PM fallback test

**Files:**
- Modify: `tests/test_memory_log.py:719-729`

- [ ] **Step 1: Verify the breakage**

Run: `pytest tests/test_memory_log.py::TestPortfolioManager::test_pm_falls_back_to_freetext_when_structured_unavailable -v`

Expected: FAIL after Tasks 1-2 are in, because `plain_response` ("**Rating**: Sell\n\nExit ahead of guidance." — 41 chars) is now below the 80-char threshold and triggers `StructuredOutputEmpty`. If the test name differs (the file uses other PM helpers), use `pytest tests/test_memory_log.py -v -k "pm_falls_back" `.

This step is a *verification step*, not a setup step — it confirms the failure mode we are fixing actually exists. Do not proceed to Step 2 until you see the failure with the exact reason above.

- [ ] **Step 2: Extend the plain_response to clear the threshold**

Edit `tests/test_memory_log.py:723`. Replace:

```python
        plain_response = "**Rating**: Sell\n\nExit ahead of guidance."
```

with:

```python
        # Keep this at >= 80 chars (the threshold enforced in
        # tradingagents/agents/utils/structured.py:_MIN_RENDERED_CHARS); a
        # too-short fallback now raises StructuredOutputEmpty by design.
        plain_response = (
            "**Rating**: Sell\n\n"
            "Exit ahead of guidance: margin trajectory deteriorating, "
            "no near-term catalyst, downside risk unbalanced."
        )
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `pytest tests/test_memory_log.py::TestPortfolioManager::test_pm_falls_back_to_freetext_when_structured_unavailable -v`

Expected: PASS.

- [ ] **Step 4: Confirm no other test in this file regresses**

Run: `pytest tests/test_memory_log.py -v`

Expected: all PASS. The `Reflector` tests with short content (lines 464, 475, 614) do not go through `invoke_structured_or_freetext` and remain unaffected, but the full run confirms that empirically.

- [ ] **Step 5: Commit**

```bash
git add tests/test_memory_log.py
git commit -m "test(pm): lengthen freetext-fallback fixture above empty threshold

The PM freetext-fallback test used a 41-char response which now trips
the 80-char StructuredOutputEmpty guard. Extends the response to a
realistic length so the test exercises the intended fallback path."
```

---

## Task 5: Self-healing structured-output wrapper — module-level pieces

**Files:**
- Modify: `tradingagents/llm_clients/gonka_client.py`
- Test: `tests/test_gonka_client.py`

This task adds the module-level cache, the error-hint constant, and the `_LearningStructuredRunnable` wrapper class — but does NOT yet wire them into `GonkaStreamSafeChatOpenAI`. That wiring lands in Task 6. Splitting lets each piece commit with its own focused test.

- [ ] **Step 1: Write the failing test**

Open `tests/test_gonka_client.py`. After the existing tests in the file, append:

```python
# ---------------------------------------------------------------------------
# Self-healing structured-output wrapper (see
# dev_notes/gonka-structured-output-resilience-design.md, Component 1)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock
import pytest


@pytest.fixture(autouse=False)
def clear_broken_backends_cache():
    """Reset the process-level cache between tests in this class so each
    case starts from a clean state."""
    from tradingagents.llm_clients import gonka_client as gc
    gc._BACKENDS_WITHOUT_TOOL_CALLING.clear()
    yield
    gc._BACKENDS_WITHOUT_TOOL_CALLING.clear()


@pytest.mark.unit
class TestLearningStructuredRunnable:
    """The wrapper detects the vLLM 'auto tool choice' error on first
    invoke, switches to a json_mode runnable, and caches that decision
    for the process lifetime so subsequent calls skip the function_calling
    attempt entirely."""

    def test_module_state_exists(self):
        from tradingagents.llm_clients import gonka_client as gc
        assert isinstance(gc._BACKENDS_WITHOUT_TOOL_CALLING, set)
        assert "tool choice requires --enable-auto-tool-choice" in gc._VLLM_TOOL_CHOICE_ERROR_HINT

    def test_first_invoke_downgrades_on_vllm_tool_choice_error(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError(
            'Error: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
        )

        # Stand in for the host LLM. We only need the attributes the
        # wrapper reads (model_name, openai_api_base) and a downgrade
        # builder it can call.
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"
        downgraded = MagicMock()
        downgraded.invoke.return_value = "json_mode_result"

        wrapper = _LearningStructuredRunnable(
            primary=primary,
            host=host,
            schema=object,  # opaque; the downgrade builder is patched below
            extra_kwargs={},
        )
        # Patch the json_mode build so we do not require a real LLM.
        wrapper._build_json_mode = lambda: downgraded

        result = wrapper.invoke("prompt")
        assert result == "json_mode_result"
        # Primary was tried exactly once before the downgrade.
        primary.invoke.assert_called_once_with("prompt")
        # Downgrade was invoked exactly once with the same prompt.
        downgraded.invoke.assert_called_once_with("prompt")
        # Cache now reflects the broken backend.
        assert ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1") in _BACKENDS_WITHOUT_TOOL_CALLING

    def test_subsequent_invoke_skips_primary(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import _LearningStructuredRunnable

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError(
            'Error: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
        )
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"
        downgraded = MagicMock()
        downgraded.invoke.return_value = "json_mode_result"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: downgraded

        wrapper.invoke("prompt-1")
        wrapper.invoke("prompt-2")

        # Primary was attempted only on the first call.
        assert primary.invoke.call_count == 1
        # Downgrade handled both calls.
        assert downgraded.invoke.call_count == 2
        downgraded.invoke.assert_called_with("prompt-2")

    def test_unrelated_error_propagates_and_does_not_cache(self, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.side_effect = RuntimeError("503 Service Unavailable")
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: (_ for _ in ()).throw(
            AssertionError("downgrade builder must not be called")
        )

        with pytest.raises(RuntimeError, match="503"):
            wrapper.invoke("prompt")
        assert ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1") not in _BACKENDS_WITHOUT_TOOL_CALLING

    def test_cache_clear_simulates_process_restart(self, clear_broken_backends_cache):
        """When the cache is empty (process just started) and the primary
        now succeeds (Gonka has been fixed), function_calling is used and
        the cache stays empty — no code change required on the client side."""
        from tradingagents.llm_clients.gonka_client import (
            _LearningStructuredRunnable,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )

        primary = MagicMock()
        primary.invoke.return_value = "function_calling_result"
        host = MagicMock()
        host.model_name = "moonshotai/Kimi-K2.6"
        host.openai_api_base = "https://router.gonkascan.com/v1"

        wrapper = _LearningStructuredRunnable(
            primary=primary, host=host, schema=object, extra_kwargs={}
        )
        wrapper._build_json_mode = lambda: (_ for _ in ()).throw(
            AssertionError("downgrade builder must not be called")
        )

        result = wrapper.invoke("prompt")
        assert result == "function_calling_result"
        # Cache stays empty — no broken-backend observation was made.
        assert len(_BACKENDS_WITHOUT_TOOL_CALLING) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_gonka_client.py::TestLearningStructuredRunnable -v`

Expected: all 5 tests FAIL with `ImportError` or `AttributeError` on `_BACKENDS_WITHOUT_TOOL_CALLING` / `_VLLM_TOOL_CHOICE_ERROR_HINT` / `_LearningStructuredRunnable`.

- [ ] **Step 3: Add the module-level pieces**

Edit `tradingagents/llm_clients/gonka_client.py`. Find the existing `class GonkaStreamSafeChatOpenAI(NormalizedChatOpenAI):` declaration (around line 84). Insert the following BEFORE that class declaration (at module level):

```python
import logging

logger = logging.getLogger(__name__)

# Class-level cache of (model_name, base_url) pairs that have been
# observed to fail the function_calling structured-output path with the
# specific vLLM "auto tool choice requires --enable-auto-tool-choice
# and --tool-call-parser" error. Entries persist for the process
# lifetime only — restarting picks up upstream fixes automatically.
# We deliberately do not persist this to disk: when the Gonka model
# operators add the flags, a normal process restart resumes
# function_calling with no code or config change on the client side.
_BACKENDS_WITHOUT_TOOL_CALLING: set[tuple[str, str]] = set()

# Substring uniquely identifying the vLLM serving-chat refusal. This is
# the literal wording from vllm/entrypoints/openai/serving_chat.py —
# narrow enough not to false-positive on generic 400s, broad enough to
# survive minor wording changes across vLLM versions. If vLLM ever
# rewords this line, the wrapper will stop downgrading and the original
# exception will propagate; the fix is updating this one constant.
_VLLM_TOOL_CHOICE_ERROR_HINT = "tool choice requires --enable-auto-tool-choice"


class _LearningStructuredRunnable:
    """Wraps a structured-output runnable so the first invocation can
    detect a backend that lacks vLLM tool-calling flags, rebind the
    schema with json_mode, and cache that discovery for the rest of the
    process.

    See dev_notes/gonka-structured-output-resilience-design.md
    (Component 1) for the full design rationale.
    """

    def __init__(self, primary, host, schema, extra_kwargs):
        self._primary = primary
        self._host = host
        self._schema = schema
        self._extra_kwargs = extra_kwargs
        self._downgraded = None  # built lazily on first downgrade

    def _build_json_mode(self):
        # Bypass GonkaStreamSafeChatOpenAI.with_structured_output to
        # avoid re-wrapping ourselves recursively. Reaching directly
        # into NormalizedChatOpenAI gives us a plain json_mode binding.
        return NormalizedChatOpenAI.with_structured_output(
            self._host, self._schema, method="json_mode", **self._extra_kwargs
        )

    def invoke(self, prompt, *args, **kwargs):
        if self._downgraded is not None:
            return self._downgraded.invoke(prompt, *args, **kwargs)
        try:
            return self._primary.invoke(prompt, *args, **kwargs)
        except Exception as exc:
            if _VLLM_TOOL_CHOICE_ERROR_HINT not in str(exc):
                raise
            logger.warning(
                "%s on %s: vLLM rejected tool-calling structured output (%s); "
                "downgrading to json_mode for the lifetime of this process. "
                "Next process start will re-probe — if the backend has been "
                "fixed, function_calling will be used again automatically.",
                self._host.model_name, self._host.openai_api_base, exc,
            )
            _BACKENDS_WITHOUT_TOOL_CALLING.add(
                (self._host.model_name, str(self._host.openai_api_base or ""))
            )
            self._downgraded = self._build_json_mode()
            return self._downgraded.invoke(prompt, *args, **kwargs)
```

If `NormalizedChatOpenAI` is not yet imported in this file, ensure the import is present (the existing code at line 34 already does `from .openai_client import NormalizedChatOpenAI` per the spec's reading — verify before adding a duplicate import).

If `logging` / `logger` are already imported at the top of the file, do not re-import — reuse the existing logger.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_gonka_client.py::TestLearningStructuredRunnable -v`

Expected: all 5 PASS.

Run the whole gonka client test file to confirm no regression:

Run: `pytest tests/test_gonka_client.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_clients/gonka_client.py tests/test_gonka_client.py
git commit -m "feat(gonka): _LearningStructuredRunnable for self-healing tool-call downgrade

Adds the wrapper class that detects vLLM 'auto tool choice requires
--enable-auto-tool-choice' on first invoke and switches to a json_mode
runnable for the rest of the process. Module-level state and constants
land here; wiring into GonkaStreamSafeChatOpenAI.with_structured_output
follows in the next commit."
```

---

## Task 6: Wire the wrapper into `GonkaStreamSafeChatOpenAI`

**Files:**
- Modify: `tradingagents/llm_clients/gonka_client.py` — `GonkaStreamSafeChatOpenAI` class
- Test: `tests/test_gonka_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestLearningStructuredRunnable` (or alongside it as a new class — choose based on whether the fixture scope still applies):

```python
@pytest.mark.unit
class TestGonkaWithStructuredOutputIntegration:
    """GonkaStreamSafeChatOpenAI.with_structured_output consults the
    cache (skipping function_calling for known-broken backends) and
    wraps every returned runnable so first-call discovery still works
    for backends not yet in the cache."""

    def test_returns_learning_wrapper(self, monkeypatch, clear_broken_backends_cache):
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _LearningStructuredRunnable,
        )
        # Build a minimal instance that does not need a real network: we
        # only call with_structured_output and inspect the return.
        # GonkaStreamSafeChatOpenAI(__init__) requires the langchain
        # ChatOpenAI kwargs — pass enough to satisfy it, but patch the
        # network-touching pieces.
        # We monkeypatch the superclass with_structured_output to return
        # a sentinel so we know the wrapper wraps it.
        sentinel_primary = MagicMock(name="primary_runnable")
        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            lambda self, schema, **kwargs: sentinel_primary,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        wrapped = host.with_structured_output(object)
        assert isinstance(wrapped, _LearningStructuredRunnable)
        assert wrapped._primary is sentinel_primary

    def test_cached_backend_uses_json_mode_method(self, monkeypatch, clear_broken_backends_cache):
        """When the cache says this backend is broken, with_structured_output
        delegates with method='json_mode' even if the caller did not specify
        a method — so the primary runnable is already a json_mode one and
        the wrapper never has to downgrade."""
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )
        captured_kwargs = {}

        def fake_super_with(self, schema, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(name="bound_runnable")

        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            fake_super_with,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        _BACKENDS_WITHOUT_TOOL_CALLING.add(
            ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1")
        )
        host.with_structured_output(object)
        assert captured_kwargs.get("method") == "json_mode"

    def test_explicit_method_kwarg_wins_over_cache(self, monkeypatch, clear_broken_backends_cache):
        """If a caller explicitly passes method=..., we respect it. The
        cache only fills in method when the caller left it unset."""
        from tradingagents.llm_clients.gonka_client import (
            GonkaStreamSafeChatOpenAI,
            _BACKENDS_WITHOUT_TOOL_CALLING,
        )
        captured_kwargs = {}

        def fake_super_with(self, schema, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(name="bound_runnable")

        monkeypatch.setattr(
            "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI.with_structured_output",
            fake_super_with,
        )
        host = GonkaStreamSafeChatOpenAI(
            model="moonshotai/Kimi-K2.6",
            base_url="https://router.gonkascan.com/v1",
            api_key="test",
        )
        _BACKENDS_WITHOUT_TOOL_CALLING.add(
            ("moonshotai/Kimi-K2.6", "https://router.gonkascan.com/v1")
        )
        host.with_structured_output(object, method="function_calling")
        assert captured_kwargs.get("method") == "function_calling"
```

If `GonkaStreamSafeChatOpenAI(__init__)` rejects the args used above (it inherits from `ChatOpenAI` and may require richer kwargs), use `monkeypatch.setattr` on `GonkaStreamSafeChatOpenAI.__init__` to a no-op and set attributes manually, or instantiate via the helper that `tests/test_gonka_client.py` already uses (`_FakeChatOpenAI` pattern shown at the top of that file). Match whichever pattern the existing tests use.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_gonka_client.py::TestGonkaWithStructuredOutputIntegration -v`

Expected: all FAIL (host.with_structured_output not yet overridden).

- [ ] **Step 3: Override `with_structured_output` in `GonkaStreamSafeChatOpenAI`**

Edit `tradingagents/llm_clients/gonka_client.py`. Inside `class GonkaStreamSafeChatOpenAI(NormalizedChatOpenAI):` (around line 84), add the following method (keep all existing methods intact):

```python
    def with_structured_output(self, schema, *, method=None, **kwargs):
        # Self-healing structured-output dispatch — see
        # dev_notes/gonka-structured-output-resilience-design.md,
        # Component 1. When we already know this backend rejects
        # tool-calling requests, ask the superclass to bind a json_mode
        # runnable directly. Either way, wrap the result so a *new*
        # backend (or a backend that has just been fixed upstream) is
        # discovered on first invoke without any client-side change.
        cache_key = (self.model_name, str(self.openai_api_base or ""))
        if method is None and cache_key in _BACKENDS_WITHOUT_TOOL_CALLING:
            method = "json_mode"
        primary = super().with_structured_output(schema, method=method, **kwargs)
        return _LearningStructuredRunnable(
            primary=primary,
            host=self,
            schema=schema,
            extra_kwargs=kwargs,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_gonka_client.py::TestGonkaWithStructuredOutputIntegration -v`

Expected: all PASS.

Run the whole gonka client test file to confirm no regression in the existing defaults / construction tests:

Run: `pytest tests/test_gonka_client.py -v`

Expected: all PASS.

- [ ] **Step 5: Run the entire unit test suite to confirm no cross-file regression**

Run: `pytest -m unit -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/llm_clients/gonka_client.py tests/test_gonka_client.py
git commit -m "feat(gonka): wire self-healing wrapper into with_structured_output

GonkaStreamSafeChatOpenAI now consults the broken-backends cache to
pick json_mode upfront for known-bad backends and wraps every returned
runnable in _LearningStructuredRunnable so first-call discovery handles
backends not yet seen. Explicit method=... arguments still win — the
override only fills in method when the caller left it unset."
```

---

## Task 7: Integration smoke test against the live Gonka backend

This is a manual acceptance step. It is bundled into the plan so the
implementation worker knows the work is not complete until this passes;
no commit is produced.

- [ ] **Step 1: Prepare a small ticker batch**

Pick 5–10 tickers from the run-23 hard-failure list so the new code paths are exercised:

```text
AIG AMT AMZN BAC BLK COST GE MSFT
```

These tickers triggered both the tool-calling error and the stalled-stream paths in run 23, so they are the right canaries.

- [ ] **Step 2: Run the batch using Kimi via Gonka**

```bash
TRADINGAGENTS_LLM=gonka \
TRADINGAGENTS_DEEP_MODEL="moonshotai/Kimi-K2.6" \
TRADINGAGENTS_QUICK_MODEL="moonshotai/Kimi-K2.6" \
python -m app.runner --workers 4 AIG AMT AMZN BAC BLK COST GE MSFT
```

Use the env-var names the runner already reads. If the project uses different env vars, replace with the project's actual names (check `app/runner.py:_build_config`).

- [ ] **Step 3: Verify acceptance criteria**

Open `~/.tradingagents/app/decisions.sqlite3` (or the project's DB location) and run:

```sql
SELECT ticker, status, length(final_trade_decision) FROM decisions
WHERE trade_date = (SELECT max(trade_date) FROM decisions)
ORDER BY ticker;
```

Acceptance:
1. Zero rows where `status='ok'` and `length(final_trade_decision) < 80`.
2. The runner log shows exactly one warning per process start from `_LearningStructuredRunnable` (the first ticker that hit the downgrade path).
3. Subsequent tickers in the same run do NOT log the downgrade warning a second time — proving the process-level cache works.
4. Any tickers that hard-failed did so with `StructuredOutputEmpty` only after the configured retry budget was exhausted (visible in the runner log's attempt-counter lines).

- [ ] **Step 4: Compare against the Qwen reference**

Rerun the same tickers with Qwen to confirm no regression on a path that does not exercise the downgrade:

```bash
TRADINGAGENTS_LLM=gonka \
TRADINGAGENTS_DEEP_MODEL="Qwen/Qwen3-235B-A22B-Instruct-2507-FP8" \
TRADINGAGENTS_QUICK_MODEL="Qwen/Qwen3-235B-A22B-Instruct-2507-FP8" \
python -m app.runner --workers 4 AIG AMT AMZN BAC BLK COST GE MSFT
```

Acceptance: success rate matches or exceeds the run-23 Qwen baseline; no `_LearningStructuredRunnable` warning is logged (the cache stays empty because the Qwen backend does not trip the vLLM hint).

- [ ] **Step 5: Mark the plan complete**

Update the spec's status line to "implemented" (`dev_notes/gonka-structured-output-resilience-design.md:3`) and commit:

```bash
git add dev_notes/gonka-structured-output-resilience-design.md
git commit -m "docs(gonka): mark structured-output resilience spec as implemented"
```

---

## Self-review checklist

- [x] Spec section "Component 1" → Task 5 + Task 6
- [x] Spec section "Component 2" → Task 1 + Task 2
- [x] Spec section "Component 3" (no change) → no task, documented in File map
- [x] Spec section "Component 4" → Task 3
- [x] Spec testing section (`tests/test_gonka_client.py`) → Task 5 + Task 6
- [x] Spec testing section (`tests/test_structured_agents.py`) → Task 2
- [x] Spec testing section (`tests/test_runner.py` — renamed to `test_runner_retry.py` since no such file existed) → Task 3
- [x] Spec testing section (`tests/test_memory_log.py` audit) → Task 4
- [x] Spec acceptance "small batch (5–10 tickers)" → Task 7

All exception types, marker strings, and method signatures are consistent across tasks:
- `StructuredOutputEmpty` always inherits from `ValueError`
- Marker substring `"no usable content from structured output"` appears identically in `structured.py` (Task 1/2), the exception message (Task 2), the runner whitelist (Task 3), and the contract test (Task 1)
- `_BACKENDS_WITHOUT_TOOL_CALLING` and `_VLLM_TOOL_CHOICE_ERROR_HINT` names match between gonka_client.py (Task 5) and the tests (Task 5/6)
- `_LearningStructuredRunnable.__init__` signature `(primary, host, schema, extra_kwargs)` is consistent in Tasks 5 and 6

No placeholders, no "TBD", no "implement later". Each step contains the code or command to run.
