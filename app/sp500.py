"""S&P 500 ticker universe for the daily analysis app.

The default "top 20" list below is a static snapshot of the largest S&P 500
constituents by market capitalisation as of early 2026. We deliberately ship a
static list rather than scraping Wikipedia or hitting an index-provider API on
every run — the universe rotates slowly, scraping introduces a flaky dependency
on the schedule path, and the user can always override via env or pass an
explicit list.

Override mechanisms (in priority order):
    1. ``SP500_TICKERS`` env var — comma-separated list, e.g.
       ``SP500_TICKERS=NVDA,AAPL,MSFT``.
    2. ``get_top_tickers(n)`` argument.
"""

from __future__ import annotations

import os
from typing import List


_TOP_BY_MARKET_CAP: List[str] = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "AMZN",
    "META", "AVGO", "BRK.B", "TSLA", "LLY",
    "JPM", "WMT", "V", "ORCL", "MA",
    "XOM", "COST", "PG", "JNJ", "HD",
    "NFLX", "BAC", "ABBV", "CRM", "CVX",
    "KO", "AMD", "PEP", "MRK", "TMO",
]


def _from_env() -> List[str] | None:
    raw = os.environ.get("SP500_TICKERS")
    if not raw:
        return None
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return parts or None


def get_top_tickers(n: int = 20) -> List[str]:
    """Return the top ``n`` tickers for the daily run.

    Env var ``SP500_TICKERS`` (comma-separated) overrides both the static list
    and the count when set, which is the easy way to point a one-off run at a
    custom watchlist without code changes.
    """
    env_list = _from_env()
    if env_list is not None:
        return env_list
    if n <= 0:
        return []
    return _TOP_BY_MARKET_CAP[:n]
