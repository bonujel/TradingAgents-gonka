"""Detection of pathologically degenerate model output.

A flaky Gonka executor (or an FP8-quant numerical blow-up) occasionally
returns a *successful* HTTP 200 whose body is broken text — either a
repetition loop ("you are a user, you are a user, ...") or token salad
("的的的的 the the )) are) are) ..."). Because the HTTP call succeeded,
the transport-level retry layers never see it and the garbage gets
persisted as an agent report.

This module spots that garbage from the finalized report text so the
caller can raise :class:`DegenerateOutputError` and let the LangGraph
node-level retry re-roll on a different executor.

Enforcement is **opt-in and off by default** — see ``_detection_enabled``.
On a bad backend day the detector turns silently-degraded reports into
hard run failures, which is noisier than the previous behaviour, so it
is gated behind ``TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT=1``.

Retry-budget note (applies when enforcement is on): ``DegenerateOutputError``
is retryable at the **node** level only (``tradingagents/graph/setup.py``),
bounded by that node's ``RetryPolicy.max_attempts``. It is deliberately
excluded from the **ticker**-level retry in ``app/runner.py`` so a
persistently bad backend fails fast after a few node re-rolls instead of
also re-running the whole pipeline.
"""

from __future__ import annotations

import os
from collections import Counter


class DegenerateOutputError(RuntimeError):
    """Raised when a finalized agent report is degenerate model output.

    Node-level retryable; not ticker-level retryable — see the module
    docstring for the retry-budget rationale.
    """


# Enforcement is opt-in. Turning a silently-degraded report into a hard
# DegenerateOutputError (retried, then failing the run) spiked the
# observed failure rate when the Gonka backend was having a bad day —
# previously that garbage was just persisted. Disabled by default;
# re-enable with TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT=1 when backend
# output quality is being actively investigated.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _detection_enabled() -> bool:
    """Whether ``check_not_degenerate`` actually enforces (read per call
    so the env var can be toggled without reimporting)."""
    return (
        os.environ.get("TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT", "0")
        .strip()
        .lower()
        in _TRUTHY
    )


# Only sufficiently long output is judged. A real degeneration loop runs
# until it fills the token budget, so genuine garbage is always long;
# gating on length keeps short, legitimately-terse reports untouched
# (near-empty output is already handled by the structured-output
# minimum-length guard).
_MIN_CHARS = 600
_MIN_WORDS = 200

# Thresholds are deliberately conservative — set well clear of healthy
# prose so a normal report never trips, accepting that a mildly-degraded
# output may slip through. The observed garbage is egregious and clears
# these by a wide margin.
_MIN_UNIQUE_WORD_RATIO = 0.15   # healthy prose ~0.3-0.6
_MAX_TOP_WORD_SHARE = 0.30      # healthy English tops ~0.06-0.08 ("the")
_MAX_TOP_CHAR_SHARE = 0.25      # healthy text tops ~0.10-0.13 on one letter
_MAX_TOP_BIGRAM_SHARE = 0.06    # healthy prose tops ~0.01-0.02 ("of the")


def is_degenerate_text(text: str | None) -> bool:
    """Return ``True`` when ``text`` looks like a repetition loop or salad.

    Four independent signals, any of which trips:

    1. **Vocabulary collapse** — the ratio of distinct words is far below
       what coherent prose sustains. Catches repetition loops.
    2. **Single-word domination** — one token is an implausible share of
       all words.
    3. **Character-level collapse** — one character dominates. Catches
       CJK / no-whitespace salad ("的的的的...") that word-splitting on
       whitespace cannot see; this signal runs even when there are too
       few whitespace-delimited words for signals 1, 2 and 4.
    4. **Bigram domination** — one consecutive word pair recurs far past
       what prose does. Catches token salad that keeps a broad-ish
       vocabulary ("the the )) are) are) ...") and so survives signal 1.
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_CHARS:
        return False

    # Signal 3 — character-level. Runs for any long text, including CJK
    # output that has no whitespace word boundaries.
    chars = [c for c in stripped if not c.isspace()]
    if chars:
        top_char_share = Counter(chars).most_common(1)[0][1] / len(chars)
        if top_char_share > _MAX_TOP_CHAR_SHARE:
            return True

    # Signals 1, 2 & 4 — word-level. Need enough whitespace-delimited
    # words to be meaningful.
    words = [w.lower() for w in stripped.split()]
    n = len(words)
    if n >= _MIN_WORDS:
        unique_ratio = len(set(words)) / n
        if unique_ratio < _MIN_UNIQUE_WORD_RATIO:
            return True
        top_word_share = Counter(words).most_common(1)[0][1] / n
        if top_word_share > _MAX_TOP_WORD_SHARE:
            return True
        bigrams = list(zip(words, words[1:]))
        top_bigram_share = Counter(bigrams).most_common(1)[0][1] / len(bigrams)
        if top_bigram_share > _MAX_TOP_BIGRAM_SHARE:
            return True

    return False


def check_not_degenerate(text: str | None, agent_name: str) -> None:
    """Raise :class:`DegenerateOutputError` when ``text`` is degenerate.

    A no-op unless ``TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT`` is truthy —
    see ``_detection_enabled``. When enforcing, the message is purely
    factual: it does not promise a retry, because the same string is
    what gets persisted if every node-level re-roll is exhausted. Retry
    semantics are the caller's concern (see ``tradingagents/graph/setup.py``).
    """
    if not _detection_enabled():
        return
    if is_degenerate_text(text):
        raise DegenerateOutputError(
            f"{agent_name}: model produced degenerate output "
            f"({len((text or '').strip())} chars of repetition / token salad)"
        )
