"""Unit tests for dashboard-related db helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "decisions.sqlite3"
    db.init_db(db_path)
    return db_path


def _insert(
    path: Path,
    *,
    ticker: str,
    trade_date: str,
    rating: str | None = "Hold",
    deep_model: str | None = "Qwen/Qwen3",
    error: str | None = None,
) -> None:
    db.upsert_decision(
        ticker=ticker,
        trade_date=trade_date,
        rating=rating,
        final_decision=None if error else "PM verdict body",
        reports={
            "market_report": "m",
            "sentiment_report": "s",
            "news_report": "n",
            "fundamentals_report": "f",
            "investment_plan": "ip",
            "trader_plan": "tp",
        },
        deep_model=deep_model,
        error=error,
        path=path,
    )


def test_count_distinct_dates_empty(tmp_db: Path):
    assert db.count_distinct_dates(path=tmp_db) == 0


def test_count_distinct_dates_counts_unique_dates(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25")
    assert db.count_distinct_dates(path=tmp_db) == 2


def test_count_distinct_tickers_empty(tmp_db: Path):
    assert db.count_distinct_tickers(path=tmp_db) == 0


def test_count_distinct_tickers_counts_unique_tickers(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26")
    assert db.count_distinct_tickers(path=tmp_db) == 2


def test_get_latest_decision_per_ticker_returns_latest_per_ticker(tmp_db: Path):
    # AAPL has rows on two dates — caller should get the newer one.
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25", rating="Hold")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", rating="Buy")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26", rating="Sell")

    result = db.get_latest_decision_per_ticker(
        tickers=["AAPL", "MSFT"], path=tmp_db
    )
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert result["AAPL"]["trade_date"] == "2026-05-26"
    assert result["AAPL"]["rating"] == "Buy"
    assert result["MSFT"]["rating"] == "Sell"


def test_get_latest_decision_per_ticker_excludes_unknown_tickers(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    result = db.get_latest_decision_per_ticker(
        tickers=["AAPL", "NOPE"], path=tmp_db
    )
    assert set(result.keys()) == {"AAPL"}


def test_get_latest_decision_per_ticker_handles_empty_list(tmp_db: Path):
    assert db.get_latest_decision_per_ticker(tickers=[], path=tmp_db) == {}


def test_get_latest_decision_per_ticker_normalises_case(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    result = db.get_latest_decision_per_ticker(
        tickers=["aapl"], path=tmp_db
    )
    assert "AAPL" in result


def test_get_latest_decision_per_ticker_includes_has_error(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", error=None)
    _insert(tmp_db, ticker="BAD", trade_date="2026-05-26", error="boom")
    result = db.get_latest_decision_per_ticker(
        tickers=["AAPL", "BAD"], path=tmp_db
    )
    assert result["AAPL"]["has_error"] == 0
    assert result["BAD"]["has_error"] == 1
