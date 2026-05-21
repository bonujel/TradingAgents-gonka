# Gonka structured-output resilience — design

**Status:** proposed (2026-05-21)
**Author:** brainstorm session
**Related code:** `tradingagents/llm_clients/gonka_client.py`, `tradingagents/llm_clients/openai_client.py`, `tradingagents/llm_clients/capabilities.py`, `tradingagents/agents/utils/structured.py`, `app/runner.py`
**Related notes:** `dev_notes/kimi-run-23-2026-05-20.md`

## Problem

Run 23 (2026-05-20, 100 ticker batch, `moonshotai/Kimi-K2.6` via Gonka router,
`max_workers=16`) surfaced two distinct client-side failure modes:

1. **vLLM tool-calling not configured (14 hard failures).** Analyst nodes
   using `llm.bind_tools(...)` and decision-making agents
   (Trader / Research Manager / Portfolio Manager) using
   `with_structured_output(...)` with the default function-calling method
   both received the literal vLLM serving-chat refusal:

   ```
   "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
   ```

   Root cause is upstream: the vLLM instances behind the Gonka router
   serving Kimi-K2.6 were not launched with `--enable-auto-tool-choice`
   / `--tool-call-parser`. Any request carrying a `tools` array is
   rejected.

2. **Empty / truncated structured-output responses written as successful decisions (54 soft failures).**
   `tradingagents/agents/utils/structured.py:invoke_structured_or_freetext`
   falls back from `with_structured_output(...)` to a plain
   `llm.invoke(prompt)` on any exception. The fallback returns
   `response.content` without checking it; when the upstream stream is
   truncated or stalled, `response.content` is `""` or a few characters.
   That empty string flows through Trader / RM / PM into
   `final_trade_decision` in the DB. The runner only inspects whether
   `ta.propagate(...)` raised — empty decisions pass as `ok`.

The two failure modes have different ownership:

- The vLLM flags issue is **owned by Gonka model operators**. Application
  code cannot fix it; it can only avoid tripping over it.
- The empty-fallback issue is **owned by this application**, regardless of
  which upstream provider is in use.

A previous draft attempted to solve (1) by adding `moonshotai/Kimi-K2.6`
to the global `_BY_ID` capability table with
`preferred_structured_method="json_mode"`. That draft was rejected
because it conflates "this model's inherent protocol-level capabilities"
(what the table is designed to express) with "this deployment of this
model is temporarily misconfigured" (a per-provider runtime fact). When
the Gonka operators fix the backend, a global table entry would have to
be remembered and reverted — exactly the kind of stale workaround that
rots in codebases.

## Goals

1. When Gonka backend lacks vLLM tool-calling flags, decision-making
   agents (Trader, Research Manager, Portfolio Manager) automatically
   downgrade their structured output path to a non-tool-using method,
   without any code or config change required at first failure.
2. When Gonka backend is fixed (flags added at any future date, no
   advance notice), this client returns to using the better
   function-calling path automatically on the next process start. No
   code change, no env change, no config change.
3. Empty or trivially short responses from the structured/free-text
   helper are treated as retryable failures by the runner, not silently
   written into the DB as successful decisions.
4. The model-id-keyed capability table (`capabilities.py`) remains
   limited to model-inherent protocol-level facts — no Gonka-deployment
   workarounds leak into it.

## Non-goals

1. **Analyst tool-calling refactor.** The News / Market / Fundamentals
   Analyst nodes call `llm.bind_tools(tools)` directly to fetch data.
   When the Gonka backend lacks tool-calling flags, these analysts hard
   fail (no retry possible). Converting them to a "prefetch + prompt
   injection" pattern (mirroring the existing Sentiment Analyst) is a
   separate, larger change that is out of scope for this spec.

2. **Stream stalled / winner-stalled mitigation.** Run 23 logged 79
   attempt-level stalled retries and 12 tickers that failed after
   four attempts. Reducing this rate involves Gonka router timeouts,
   winner-failover policy, and vLLM streaming chunk configuration —
   all upstream. Application-side mitigation is limited to existing
   runner backoff and is out of scope here.

3. **Dashboard `ok_empty_final` indicator.** Once empty responses
   become explicit retryable failures (goal 3 above), the
   `ok_empty_final` category collapses naturally. A dashboard column
   explicitly distinguishing "ok with all reports" from "ok with some
   short reports" can be added in a follow-up if useful, but is not
   needed for this design to be correct.

4. **Gonka-side configuration changes.** Confirmation message to model
   operators has already been drafted separately.

## Design

Two independent changes, decoupled deliberately so each can be reasoned
about and reverted on its own timeline.

### Component 1 — Self-healing structured-output downgrade

Lives in `tradingagents/llm_clients/gonka_client.py`, scoped to the
`GonkaStreamSafeChatOpenAI` subclass. Does not touch
`NormalizedChatOpenAI`, `capabilities.py`, or any other provider.

**State machine (per process):**

- Process-level set
  `_BACKENDS_WITHOUT_TOOL_CALLING: set[tuple[str, str]]` keyed on
  `(model_name, base_url)`. Empty at process start.
- `GonkaStreamSafeChatOpenAI.with_structured_output(schema)` consults
  the set:
  - If `(model_name, base_url)` is present, override `method="json_mode"`
    before delegating to the superclass.
  - Otherwise, delegate to the superclass with the caller's `method`
    (langchain default is `"function_calling"`).
- The returned runnable is wrapped in `_LearningStructuredRunnable`.
- On the wrapped runnable's first `invoke`:
  - Delegate to the primary runnable.
  - If the primary raises an exception whose `str(exc)` contains
    `"tool choice requires --enable-auto-tool-choice"`:
    - Log a single warning naming the model, base URL, and the
      observation that the process will use `json_mode` going forward.
    - Add `(model_name, base_url)` to
      `_BACKENDS_WITHOUT_TOOL_CALLING`.
    - Build a `json_mode` runnable using
      `NormalizedChatOpenAI.with_structured_output(self.host, schema, method="json_mode")`
      (bypassing the override on `GonkaStreamSafeChatOpenAI` to avoid
      re-wrapping).
    - Invoke the json_mode runnable with the same prompt and return
      its result.
  - If the primary raises any other exception, re-raise unchanged.
- After downgrade, the wrapped runnable holds a reference to the
  json_mode runnable. Subsequent `invoke` calls on the same wrapper
  instance go straight to it (no re-attempt of function_calling).

**Why this satisfies "Gonka fixes upstream, I do nothing":**

When operators add the flags, the next process start finds
`_BACKENDS_WITHOUT_TOOL_CALLING` empty. The first
`with_structured_output(...).invoke(...)` uses function_calling, which
now succeeds. The downgrade path never fires. The cache stays empty.
No code, env, or config change required on the client side.

**Why the error-string match is durable:**

The substring `"tool choice requires --enable-auto-tool-choice"` is
the exact wording from vLLM's
`vllm/entrypoints/openai/serving_chat.py`. It has been stable across
vLLM versions since tool-calling support landed. If vLLM ever changes
this wording, the match will silently fail — the structured-output
call will raise as if no downgrade existed, and the application will
see the same hard failure that motivated this spec. That outcome is
recoverable by updating one string constant; the architecture does
not need to change.

**Forwarded runnable methods:**

The wrapper forwards `invoke` only. Trader / RM / PM all use
`.invoke()` — no `.stream()` / `.batch()` / `.with_config()` usage
anywhere in the agent factories. A blanket `__getattr__` would
silently mask real integration bugs (langchain's Runnable surface is
large). If a caller needs another method, add it explicitly in a
follow-up — one line each.

### Component 2 — Empty-content hard fail in the structured helper

Lives in `tradingagents/agents/utils/structured.py`.

**New exports:**

- `_MIN_RENDERED_CHARS = 80` — module-level constant.
- `class StructuredOutputEmpty(ValueError)` — raised when both the
  structured-path render and the free-text fallback produce fewer than
  `min_chars` non-whitespace characters. Subclasses `ValueError`
  (not `RuntimeError`) so it lines up with the runner's existing
  retryable-`ValueError`-marker pattern (see Component 4 below).

**Modified `invoke_structured_or_freetext`:**

- New parameter `min_chars: int = _MIN_RENDERED_CHARS`. Callers do not
  need to set it; the default applies uniformly to Trader / RM / PM.
- Structured path: if `render(result).strip()` length ≥ `min_chars`,
  return it. Otherwise log a warning and fall through to the
  free-text path (treating the short render as if the structured call
  had raised).
- Free-text path: if `response.content` (after strip) length is
  < `min_chars`, raise `StructuredOutputEmpty`. The exception
  message MUST contain the stable marker substring
  `"no usable content from structured output"` so the runner's
  `_RETRYABLE_VALUE_ERROR_MARKERS` whitelist can identify it as a
  transient (typically stream-truncation) failure and engage the
  existing 5s → 15s → 45s backoff. See Component 4.
- Otherwise, return `response.content`.

**Threshold rationale:**

Run 23 evidence:

- Shortest legitimately useful final-decision observed: ~320 chars.
- Degenerate outputs were either fully empty or under ~240 chars and
  visibly truncated mid-sentence.
- 80 is well below the legitimate floor while still catching
  empty/near-empty strings unambiguously.

The parameter shape (`min_chars=int`) allows per-agent override if
some future agent legitimately produces shorter output; no current
agent needs it.

### Component 3 — Capability table unchanged

`tradingagents/llm_clients/capabilities.py` is not touched.
`moonshotai/Kimi-K2.6` continues to match `_DEFAULT`. This is
deliberate: that table expresses model-inherent protocol-level facts
(DeepSeek thinking models reject `tool_choice`; MiniMax M2.x only
accepts the `none/auto` enum). Kimi-K2.6 served by Moonshot's native
API supports full function calling; the brokenness is per-deployment,
not per-model.

### Component 4 — Runner retryable-marker addition

`app/runner.py:_RETRYABLE_VALUE_ERROR_MARKERS` extended by one entry:

```python
_RETRYABLE_VALUE_ERROR_MARKERS: tuple[str, ...] = (
    "No generations found in stream",
    "no usable content from structured output",  # new
)
```

This is the only runner-side change. The existing `_is_retryable`
dispatch already covers `ValueError` subclasses (line 102), so no
new branch is needed.

**Why retryable, not hard-fail:**

Empty/short responses observed in run 23 were near-universally
caused by mid-stream truncation (the same upstream root cause as
the 79 successful stalled-retry recoveries). Treating the new
exception as retryable lets the existing backoff salvage these
cases. If the failure persists across all attempts, the runner
escalates to hard failure as usual — exactly what the run 23 doc
called out as the desired outcome (no silent success on empty
decisions).

## Behavior matrix

| Gonka backend state | First structured call | Subsequent calls | After process restart |
|---|---|---|---|
| Missing vLLM flags (current) | function_calling raises vLLM error; wrapper catches, switches to json_mode, retries; returns result | Direct json_mode (no re-attempt) | Same as first call: re-learn once per process (one degraded call) |
| Flags added (future) | function_calling succeeds; cache stays empty | function_calling continues to succeed | function_calling, cache empty |
| Transient 500 / timeout from a healthy backend | Error message does NOT contain the vLLM hint; wrapper re-raises; runner retries normally | Unaffected | Unaffected |
| vLLM changes the error wording | Hint substring no longer matches; wrapper re-raises; behavior identical to a generic failure (runner retries, eventually hard-fails) | Same | Same — recovery is updating one constant |

## Testing

### `tests/test_gonka_client.py` (new or extended)

`TestStructuredOutputAutoDowngrade`:

1. **First call downgrades on vLLM tool-choice error.** Patch
   `NormalizedChatOpenAI.with_structured_output` so the
   function_calling path returns a stub whose `invoke` raises an
   exception with the vLLM hint string, and the json_mode path
   returns a stub that returns a valid schema instance. Assert:
   `with_structured_output(schema).invoke(prompt)` returns the
   json_mode stub's result, and `(model, base_url)` is now in
   `_BACKENDS_WITHOUT_TOOL_CALLING`.

2. **Subsequent call skips function_calling entirely.** Pre-populate
   the cache. Assert that the function_calling runnable is never
   invoked (mock `call_count == 0`).

3. **Unrelated error does not trigger downgrade.** Have the primary
   raise a generic exception whose text does not contain the hint.
   Assert: exception propagates unchanged, cache remains empty, next
   call still attempts function_calling first.

4. **Cache reset simulates process restart.** Clear the cache; have
   the primary now succeed (simulating Gonka fixing the backend);
   assert function_calling is used and cache stays empty.

Setup uses `setup_method` to clear
`_BACKENDS_WITHOUT_TOOL_CALLING` between tests. `monkeypatch` for
patching the superclass.

### `tests/test_structured_agents.py` (extended)

`TestStructuredHelperEmptyContent`:

1. **Empty free-text fallback raises `StructuredOutputEmpty`.**
   `llm.with_structured_output` raises `NotImplementedError`
   (forcing the free-text branch); `llm.invoke` returns
   `MagicMock(content="")`. Assert `create_trader(llm)(state)`
   raises `StructuredOutputEmpty`.

2. **Whitespace-only fallback raises.** Same setup with
   `content="   \n\t  "`.

3. **Short structured render falls through to free-text path.**
   Structured stub returns a `TraderProposal` whose `render` output
   is under threshold; free-text stub returns a long valid response.
   Assert the long free-text response is returned (and is what the
   caller sees in `result["trader_investment_plan"]`).

4. **Same coverage on Research Manager.** Empty fallback raises.

5. **Exception is `ValueError` subclass with the runner-visible marker.**
   Assert `isinstance(exc, ValueError)` and
   `"no usable content from structured output" in str(exc)`. This is
   the explicit contract with `app/runner.py:_RETRYABLE_VALUE_ERROR_MARKERS`.

### `tests/test_runner.py` (extended)

Add one case asserting `_is_retryable(StructuredOutputEmpty("...no usable content from structured output..."))`
returns `True`. Mirrors the existing `"No generations found in stream"`
test to confirm the marker is wired through.

### `tests/test_capabilities.py`

Not modified. Kimi is not added to this table.

### `tests/test_memory_log.py`

Sweep for PM mocks that use empty `MagicMock(content="")` fallback.
Update those to a non-empty value or expect
`StructuredOutputEmpty`. This is a known follow-up before merge,
not a separate spec item.

## Migration

No data migration. No config migration. No env vars.

Drop-in deployment:

- Existing run logs (run 23's 54 `ok_empty_final` records) stay as
  historical data. New runs will surface these as hard failures
  instead of soft.
- Gonka operators are independently informed of the vLLM flags issue.
  When they fix it, no coordination required with this client — the
  self-healing path simply stops firing.

## Rollback

- **Component 1:** Revert the `gonka_client.py` block. Restores
  pre-change behavior (function_calling attempts always, no
  downgrade). Safe to do at any time; no state to clean up.
- **Component 2:** Revert the `structured.py` block. Restores
  silent-empty-pass behavior. Discouraged but always possible.
- Components are independent. Either can be reverted without
  affecting the other.

## Alternatives considered

1. **Global capability table entry for Kimi.** Rejected. Mislabels
   model-inherent capability with a deployment-specific fact;
   requires manual cleanup when upstream is fixed; would incorrectly
   penalize same-model-id usage through other providers (e.g.,
   Moonshot direct).

2. **Env-var flag (`GONKA_VLLM_TOOL_CALLING_UNAVAILABLE=1`).** Better
   than option 1 because it scopes the workaround to Gonka and
   documents the intent. Rejected because it still requires the user
   to flip the env when upstream is fixed — failing goal 2 ("no
   code, env, or config change on the client side"). Detection-based
   self-healing makes the workaround fully invisible.

3. **Persisted (on-disk) cache of broken backends.** Considered;
   rejected. On-disk persistence is harder to invalidate cleanly
   when upstream recovers and provides no value over the current
   "learn once per process" approach — process restarts are common
   enough (uvicorn reloads, manual restarts during dev) that the
   one-call learning cost is negligible.

4. **Per-call probe of backend capability before every structured
   request.** Rejected as wasteful. One extra HTTP call per
   structured invocation, even when the backend is healthy.
   Detection on actual failure is strictly cheaper.

## Open questions

None. Design has been iterated through three rounds; remaining
items (Analyst refactor, dashboard column, stream-stall mitigation)
are explicitly out of scope per the Non-goals section.

## Acceptance checklist

- [ ] `gonka_client.py`: `_BACKENDS_WITHOUT_TOOL_CALLING`,
      `_VLLM_TOOL_CHOICE_ERROR_HINT`, `_LearningStructuredRunnable`
      added; `GonkaStreamSafeChatOpenAI.with_structured_output`
      overridden to consult the cache and wrap the result
- [ ] `structured.py`: `_MIN_RENDERED_CHARS`, `StructuredOutputEmpty`
      (subclass of `ValueError`) added; `invoke_structured_or_freetext`
      enforces the threshold on both paths and raises with the
      runner-visible marker substring
- [ ] `app/runner.py`: one new entry in `_RETRYABLE_VALUE_ERROR_MARKERS`
- [ ] `capabilities.py`: unchanged
- [ ] `tests/test_gonka_client.py`: 4 new test cases for the
      auto-downgrade behavior
- [ ] `tests/test_structured_agents.py`: 5 new test cases for empty/
      short content handling (including the marker-contract check)
- [ ] `tests/test_runner.py`: 1 new test case asserting the marker
      is classified retryable
- [ ] `tests/test_memory_log.py`: PM mocks audited; any empty
      `MagicMock(content="")` fallbacks adjusted
- [ ] Run a small batch (5–10 tickers, Kimi via Gonka, current
      broken backend state) and confirm:
  - Trader / RM / PM no longer write empty `final_trade_decision`
  - Single warning logged per process on first downgrade
  - No regression on Qwen path
