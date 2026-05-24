"""Clamp LLM-supplied date arguments to a sane range.

Qwen3-235B-Instruct-FP8 served via Gonka has been observed emitting tool-call
args with dates 200 years in the future — e.g. ``curr_date='2226-05-23'``
when the agent's system prompt explicitly states ``current date is
2026-05-23`` — by inserting an extra ``2`` into the year. The downstream
vendor adapters accept whatever date string is passed, so a hallucinated
year silently returns a thin/irrelevant article batch that then has to be
ingested by the same broken model on the *next* turn. We clamp here at the
tool boundary so a single bad token doesn't poison the rest of the loop.

We keep a generous past tolerance (~3 years) so the rare backtest use case
that wants to query historical data still works.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional


logger = logging.getLogger(__name__)


# Historical analyses are legitimate; only flag dates that are absurdly old.
_PAST_TOLERANCE_DAYS = 365 * 3

# A handful of days of forward slack covers timezone/clock skew between the
# host and the agent's "current date" string; anything beyond is a model
# error. Production typical drift was 0 days, observed hallucination 73,000.
_FUTURE_TOLERANCE_DAYS = 7


def sanitize_llm_date(value: Optional[str], *, tool: str, arg: str) -> Optional[str]:
    """Return ``value`` unchanged unless it falls outside the sane range.

    * If the string doesn't parse as YYYY-MM-DD, leave it alone — the vendor
      adapter will surface its own parse error, which is informative.
    * If it's more than ``_FUTURE_TOLERANCE_DAYS`` ahead of today, clamp to
      today and warn.
    * If it's more than ``_PAST_TOLERANCE_DAYS`` behind today, clamp to the
      past-tolerance boundary and warn (rather than today, to preserve the
      caller's "historical" intent as much as possible).
    """
    if not value:
        return value
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    today = date.today()
    future_drift = (parsed - today).days
    past_drift = (today - parsed).days
    if future_drift > _FUTURE_TOLERANCE_DAYS:
        clamped = today.isoformat()
        logger.warning(
            "%s(%s=%r): %d days in the future — clamping to %s. "
            "Likely an LLM hallucination.",
            tool, arg, value, future_drift, clamped,
        )
        return clamped
    if past_drift > _PAST_TOLERANCE_DAYS:
        boundary = today - timedelta(days=_PAST_TOLERANCE_DAYS)
        clamped = boundary.isoformat()
        logger.warning(
            "%s(%s=%r): %d days in the past — clamping to %s "
            "(%d-year past tolerance).",
            tool, arg, value, past_drift, clamped, _PAST_TOLERANCE_DAYS // 365,
        )
        return clamped
    return value
