"""Tests for ``tradingagents.dataflows.stockstats_utils``.

Covers two distinct fixes landed together (2026-05-25):

1. ``YFINANCE_LOCK`` serialisation around every ``yf_retry`` call —
   prevents ``sqlite3.OperationalError: database is locked`` from racing
   ThreadPoolExecutor workers against yfinance's peewee/SQLite cache.

2. ``_normalize_date_column`` — accepts any of the date-column aliases
   yfinance / pandas have produced (``Date``, ``Datetime``, ``index``,
   ``Unnamed: 0``, etc.) and renames it to ``Date`` so the rest of the
   pipeline (including ``stockstats.wrap`` and the per-row date
   comparison in ``StockstatsUtils.get_stock_stats``) keeps working.
   Crucially, this is enforced on BOTH read paths so legacy
   ``index,Close,...`` cache CSVs still on disk are tolerated without a
   forced rebuild.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows._yfinance_lock import YFINANCE_LOCK
from tradingagents.dataflows.stockstats_utils import (
    _clean_dataframe,
    _normalize_date_column,
    yf_retry,
)


# ---------------------------------------------------------------------------
# YFINANCE_LOCK contract: yf_retry serialises through a process-wide lock
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestYfRetryHoldsYfinanceLock:
    def test_func_runs_while_holding_lock(self):
        """The lambda passed into ``yf_retry`` must run while the
        process-wide ``YFINANCE_LOCK`` is held. ``RLock.acquire(blocking=False)``
        from a different thread returns False iff someone owns it."""
        outside_thread_could_acquire: list[bool] = []

        def probe_lock_from_other_thread() -> None:
            # blocking=False so we don't deadlock the test if the lock is
            # held — we just record whether we could grab it.
            got_it = YFINANCE_LOCK.acquire(blocking=False)
            outside_thread_could_acquire.append(got_it)
            if got_it:
                YFINANCE_LOCK.release()

        def work_under_lock() -> str:
            t = threading.Thread(target=probe_lock_from_other_thread)
            t.start()
            t.join()
            return "ok"

        result = yf_retry(work_under_lock)
        assert result == "ok"
        # Another thread could NOT have acquired the lock while we were
        # inside yf_retry, because yf_retry was already holding it.
        assert outside_thread_could_acquire == [False]

    def test_lock_released_after_success(self):
        yf_retry(lambda: None)
        # We can acquire it now → previous call released it.
        assert YFINANCE_LOCK.acquire(blocking=False)
        YFINANCE_LOCK.release()

    def test_lock_released_after_unrelated_exception(self):
        """An exception inside the lambda must not leave the lock held;
        a stuck lock would deadlock every subsequent yfinance call."""
        with pytest.raises(RuntimeError):
            yf_retry(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert YFINANCE_LOCK.acquire(blocking=False)
        YFINANCE_LOCK.release()

    def test_lock_is_reentrant(self):
        """RLock so nested yfinance calls from the same thread (yfinance
        internals do occasionally re-enter) do not self-deadlock."""
        def inner() -> str:
            return yf_retry(lambda: "nested")
        assert yf_retry(inner) == "nested"

    def test_threadpool_serialises_through_the_lock(self):
        """Smoke-tests the original bug shape: N threads invoking
        ``yf_retry`` concurrently never observe overlapping execution
        of the protected lambda."""
        in_critical = [0]
        max_observed = [0]
        in_critical_lock = threading.Lock()

        def work() -> None:
            with in_critical_lock:
                in_critical[0] += 1
                if in_critical[0] > max_observed[0]:
                    max_observed[0] = in_critical[0]
            time.sleep(0.01)
            with in_critical_lock:
                in_critical[0] -= 1

        threads = [threading.Thread(target=lambda: yf_retry(work)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Strict ==1: yf_retry serialises calls, so we never see two
        # threads inside the critical section at the same time.
        assert max_observed[0] == 1


@pytest.mark.unit
class TestYfRetryRateLimitRetry:
    """Pre-existing yf_retry contract: YFRateLimitError gets retried, other
    exceptions propagate immediately. Guard the lock change didn't break it."""

    def test_rate_limit_retried_until_success(self):
        from yfinance.exceptions import YFRateLimitError

        calls = [0]

        def flaky() -> str:
            calls[0] += 1
            if calls[0] < 3:
                raise YFRateLimitError()
            return "ok"

        with patch(
            "tradingagents.dataflows.stockstats_utils.time.sleep",
            lambda _s: None,  # no real backoff in tests
        ):
            assert yf_retry(flaky, max_retries=3, base_delay=0.0) == "ok"
        assert calls[0] == 3


# ---------------------------------------------------------------------------
# _normalize_date_column: tolerate the cache-CSV column-name drift
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeDateColumn:
    def test_index_alias_renamed_to_date(self):
        """The exact shape the production server's cache CSVs landed in
        (``header line: index,Close,High,Low,Open,Volume``)."""
        df = pd.DataFrame(
            {
                "index": ["2026-05-23", "2026-05-24"],
                "Close": [100.0, 101.0],
            }
        )
        out = _normalize_date_column(df)
        assert "Date" in out.columns
        assert "index" not in out.columns
        assert list(out["Date"]) == ["2026-05-23", "2026-05-24"]

    def test_datetime_alias_renamed_to_date(self):
        """yfinance intraday data uses ``Datetime`` instead of ``Date``."""
        df = pd.DataFrame(
            {
                "Datetime": pd.to_datetime(["2026-05-23 09:30", "2026-05-23 09:31"]),
                "Close": [100.0, 101.0],
            }
        )
        out = _normalize_date_column(df)
        assert "Date" in out.columns
        assert "Datetime" not in out.columns

    def test_unnamed_zero_alias_renamed(self):
        """``Unnamed: 0`` is what ``to_csv(index=True)`` writes when the
        index has no name; a future cache regression should still survive."""
        df = pd.DataFrame(
            {
                "Unnamed: 0": ["2026-05-23"],
                "Close": [100.0],
            }
        )
        out = _normalize_date_column(df)
        assert "Date" in out.columns

    def test_date_column_already_canonical_passes_through(self):
        df = pd.DataFrame(
            {
                "Date": ["2026-05-23"],
                "Close": [100.0],
            }
        )
        out = _normalize_date_column(df)
        assert list(out.columns) == ["Date", "Close"]

    def test_no_alias_present_passes_through_untouched(self):
        """Don't invent a Date column out of nowhere — let the caller's
        ``KeyError`` surface upstream so the real problem is obvious."""
        df = pd.DataFrame({"Close": [100.0]})
        out = _normalize_date_column(df)
        assert "Date" not in out.columns
        assert list(out.columns) == ["Close"]

    def test_date_takes_priority_over_a_stray_alias(self):
        """If both ``Date`` and an alias exist, leave it alone — renaming
        the alias would create a duplicate column and silently corrupt
        downstream selection."""
        df = pd.DataFrame(
            {
                "Date": ["2026-05-23"],
                "index": ["unrelated"],
                "Close": [100.0],
            }
        )
        out = _normalize_date_column(df)
        # Date untouched; index column left as-is for the caller to
        # decide what to do.
        assert "Date" in out.columns
        assert "index" in out.columns


# ---------------------------------------------------------------------------
# _clean_dataframe end-to-end: cache CSV with legacy `index` header gets
# salvaged transparently, no more KeyError: 'Date'
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanDataframeRecoversLegacyCacheShape:
    def test_legacy_index_header_no_longer_raises(self):
        """Reproduces the 2026-05-25 server bug: cache CSV starts with
        ``index,Close,High,Low,Open,Volume``. Before the fix this raised
        ``KeyError: 'Date'`` on every indicator computation; after, it
        returns a clean DataFrame with a parsed ``Date`` column."""
        df = pd.DataFrame(
            {
                "index": ["2026-05-23", "2026-05-24", "2026-05-25"],
                "Open": [100.0, 101.0, 102.0],
                "High": [105.0, 106.0, 107.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [104.0, 105.0, 106.0],
                "Volume": [1000, 1100, 1200],
            }
        )
        cleaned = _clean_dataframe(df)
        assert "Date" in cleaned.columns
        assert "index" not in cleaned.columns
        assert len(cleaned) == 3
        # Date is parsed to datetime so the downstream
        # ``data["Date"] <= curr_date_dt`` comparison works.
        assert pd.api.types.is_datetime64_any_dtype(cleaned["Date"])

    def test_canonical_date_header_unaffected(self):
        """The fix must not regress the case where the CSV already uses
        the canonical ``Date`` header."""
        df = pd.DataFrame(
            {
                "Date": ["2026-05-23", "2026-05-24"],
                "Open": [100.0, 101.0],
                "High": [105.0, 106.0],
                "Low": [99.0, 100.0],
                "Close": [104.0, 105.0],
                "Volume": [1000, 1100],
            }
        )
        cleaned = _clean_dataframe(df)
        assert "Date" in cleaned.columns
        assert len(cleaned) == 2
