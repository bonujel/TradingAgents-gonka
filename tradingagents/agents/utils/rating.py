"""Shared 5-tier rating vocabulary and a robust free-text rating extractor.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The Trader (transaction action; restricted to a 3-tier subset)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

The framework now relies entirely on free-text generation for these three
decision-making agents (Gonka's vLLM caps json_schema completions at ~3072
tokens, which was preventing structured output from ever succeeding on
Kimi-K2.6). That makes a high-quality free-text rating parser load-bearing
— Kimi's prose tends to weigh both sides before concluding, so a naive
first-match scan returned the wrong rating roughly half the time.

Centralising the parser here avoids drift between the call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)


# Inflected and equivalent forms commonly produced when the model writes
# naturally — "buying", "selling", "holding", and the Trader's 3-tier
# variants. Mapped to the canonical 5-tier title case the rest of the
# system stores.
_RATING_ALIASES: dict[str, str] = {
    "buy": "Buy", "buys": "Buy", "buying": "Buy", "bought": "Buy",
    "sell": "Sell", "sells": "Sell", "selling": "Sell", "sold": "Sell",
    "hold": "Hold", "holds": "Hold", "holding": "Hold",
    "overweight": "Overweight",
    "underweight": "Underweight",
}

# The alternation used inside the generic-scan regex. Inflections are
# included so prose like "we recommend buying" still resolves.
_RATING_WORD_RE = re.compile(
    r"\b(buy|buys|buying|bought|sell|sells|selling|sold|"
    r"hold|holds|holding|overweight|underweight)\b",
    re.IGNORECASE,
)


# Priority labels — the canonical headers we ask the model to emit
# directly. Captured group is the rating word.
#
# Each pattern is liberal about decoration:
#   - optional markdown bold wrappers (``**Rating**`` / ``*Rating*``)
#   - either ``:`` / ``-`` / ``is`` / ``=`` as the separator
#   - optional bold around the value (``**Buy**``)
#
# The classic ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` form
# emitted by the Trader is matched first so the trailing convention is
# honoured. All other patterns are equivalent in priority; the LAST match
# across the whole document wins (see ``parse_rating``).
_LABEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # The Trader's mandatory closer. Matched first so it shows up in the
    # last-occurrence walk regardless of any earlier label noise.
    re.compile(
        r"FINAL\s+TRANSACTION\s+PROPOSAL\s*[:\-]?\s*\**\s*"
        r"(buy|sell|hold|overweight|underweight)\b",
        re.IGNORECASE,
    ),
    # The generic header family. Each name is one the three decision
    # agents are now instructed to emit verbatim in their prompts:
    #   - Rating, Recommendation, Action            (the three primary)
    #   - Final Rating / Final Recommendation / ... (variants the model
    #     sometimes prefers in conclusions)
    #   - My Call, My Recommendation, Verdict, Stance
    #     (defensive: Kimi occasionally rephrases conclusions naturally)
    re.compile(
        r"\*{0,2}\s*(?:"
        r"final\s+(?:rating|recommendation|call|decision|position|verdict|stance|action)"
        r"|rating|recommendation|action|verdict|stance"
        r"|my\s+(?:call|recommendation|decision|verdict|stance|rating)"
        r")\s*\*{0,2}\s*(?:is|:|\-|=)\s*\**\s*"
        r"(buy|sell|hold|overweight|underweight)\b",
        re.IGNORECASE,
    ),
)


# Tokens that, when they appear in the few words immediately before a
# rating word, signal the rating is being negated or discussed in the
# abstract ("not a Buy", "rather than Sell", "avoid Hold"). The 80-char
# preceding window covers ~12-15 words, which is long enough to catch
# "I would not at this point recommend a Buy" but short enough to avoid
# bleeding into the previous sentence.
_NEGATION_RE = re.compile(
    r"\b(?:"
    r"not|no|n't|never|avoid|avoiding|against|"
    r"rather\s+than|instead\s+of|"
    r"shouldn'?t|wouldn'?t|won'?t|can'?t|cannot|don'?t|doesn'?t|didn'?t|"
    r"hardly|barely|"
    r"away\s+from"
    r")\b"
    # Allow 0-5 intervening words between the negation and the rating word.
    r"(?:\s+\w+){0,5}\s*$",
    re.IGNORECASE,
)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from free-form prose.

    Strategy (two passes; first that finds something wins):

    1. **Canonical labels** — look for ``Rating: X`` / ``Recommendation: X``
       / ``Action: X`` / ``FINAL TRANSACTION PROPOSAL: X`` etc., tolerating
       markdown bold around the label and the value, and ``is`` / ``:`` /
       ``-`` / ``=`` as the separator. The **last** label match in the
       document wins, because the model typically arrives at its
       conclusion after weighing both sides.

    2. **Generic scan** — find every rating word in the text, walk them
       from end to start, and return the first one whose preceding context
       does not look like negation ("not a Buy", "rather than Sell"). The
       end-to-start order again reflects that the final stance comes last.

    Returns a Title-cased rating from :data:`RATINGS_5_TIER`, or
    ``default`` (``"Hold"`` by convention) when nothing usable is found.
    """
    if not text:
        return default

    # Pass 1: canonical labels — last occurrence wins.
    last_label_word: str | None = None
    last_label_pos = -1
    for pattern in _LABEL_PATTERNS:
        for match in pattern.finditer(text):
            if match.start() > last_label_pos:
                last_label_pos = match.start()
                last_label_word = match.group(1)
    if last_label_word is not None:
        return _RATING_ALIASES[last_label_word.lower()]

    # Pass 2: generic scan from the end, negation-aware.
    matches = list(_RATING_WORD_RE.finditer(text))
    for match in reversed(matches):
        prefix = text[max(0, match.start() - 80):match.start()]
        if _NEGATION_RE.search(prefix):
            continue
        return _RATING_ALIASES[match.group(1).lower()]

    return default
