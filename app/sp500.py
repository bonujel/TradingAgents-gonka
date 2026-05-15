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

import csv
import os
import time
from pathlib import Path
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


_WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_CACHE_PATH = Path.home() / ".tradingagents" / "cache" / "sp500_constituents.csv"
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # refresh weekly


def _read_cache() -> List[str] | None:
    if not _CACHE_PATH.exists():
        return None
    if time.time() - _CACHE_PATH.stat().st_mtime > _CACHE_TTL_SECONDS:
        return None
    try:
        with _CACHE_PATH.open(newline="") as fh:
            rows = [row[0].strip() for row in csv.reader(fh) if row and row[0].strip()]
        return rows or None
    except (OSError, csv.Error):
        return None


def _write_cache(tickers: List[str]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("w", newline="") as fh:
            writer = csv.writer(fh)
            for t in tickers:
                writer.writerow([t])
    except OSError:
        pass  # cache is best-effort; the caller already has the data


def _fetch_from_wikipedia() -> List[str]:
    # Wikipedia returns 403 to pandas' default urllib User-Agent, so fetch with
    # requests + a real UA and hand the HTML to pandas.
    import io

    import pandas as pd
    import requests

    resp = requests.get(
        _WIKIPEDIA_SP500_URL,
        headers={"User-Agent": "TradingAgents-gonka/1.0 (S&P500 universe fetch)"},
        timeout=15,
    )
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text), match="Symbol")
    if not tables:
        raise RuntimeError("S&P 500 table not found on Wikipedia page")
    df = tables[0]
    symbols = df["Symbol"].astype(str).str.strip().tolist()
    # Wikipedia renders BRK.B / BF.B with a dot; keep that convention to match
    # the existing static list in ``_TOP_BY_MARKET_CAP``.
    return [s for s in symbols if s]


def get_sp500_tickers(force_refresh: bool = False) -> List[str]:
    """Return the full S&P 500 constituent list (~500 tickers).

    Lookup order:
        1. ``SP500_TICKERS`` env var (same override as :func:`get_top_tickers`).
        2. Disk cache at ``~/.tradingagents/cache/sp500_constituents.csv`` if
           fresh (< 7 days old) and ``force_refresh`` is false.
        3. Scrape Wikipedia and update the cache.
        4. Fall back to :data:`_TOP_BY_MARKET_CAP` when the scrape fails (e.g.
           offline). Callers should treat the return value as best-effort.

    The scrape only runs from interactive code paths (Streamlit button) — the
    scheduler/runner keeps using the static :func:`get_top_tickers`.
    """
    env_list = _from_env()
    if env_list is not None:
        return env_list

    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            return cached

    try:
        tickers = _fetch_from_wikipedia()
        if tickers:
            _write_cache(tickers)
            return tickers
    except Exception:  # noqa: BLE001 — UI button must never blow up the app
        pass

    return list(_TOP_BY_MARKET_CAP)
