"""Unit tests for db helpers that back the decision dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Fresh SQLite DB per test. Tests pass this path into db helpers."""
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
    reports: dict | None = None,
) -> None:
    db.upsert_decision(
        ticker=ticker,
        trade_date=trade_date,
        rating=rating,
        final_decision=None if error else "PM verdict body",
        reports=reports or {
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


def test_list_distinct_models_returns_unique_alphabetical(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", deep_model="Qwen/Qwen3")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26", deep_model="moonshot/kimi")
    _insert(tmp_db, ticker="NVDA", trade_date="2026-05-26", deep_model="Qwen/Qwen3")
    assert db.list_distinct_models(trade_date="2026-05-26", path=tmp_db) == [
        "Qwen/Qwen3",
        "moonshot/kimi",
    ]


def test_list_distinct_models_scopes_to_trade_date(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", deep_model="modelA")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25", deep_model="modelB")
    assert db.list_distinct_models(trade_date="2026-05-26", path=tmp_db) == ["modelA"]
    assert db.list_distinct_models(trade_date="2026-05-25", path=tmp_db) == ["modelB"]


def test_list_distinct_models_excludes_nulls(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", deep_model="modelA")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26", deep_model=None)
    assert db.list_distinct_models(trade_date="2026-05-26", path=tmp_db) == ["modelA"]


def test_list_distinct_models_empty_when_no_rows(tmp_db: Path):
    assert db.list_distinct_models(trade_date="2026-05-26", path=tmp_db) == []


def test_list_decisions_summary_paginates(tmp_db: Path):
    # Zero-padded names so string ASC ordering matches numeric — keep the padding
    # if you ever change the count.
    for i in range(25):
        _insert(tmp_db, ticker=f"T{i:02d}", trade_date="2026-05-26")

    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", page=1, page_size=10, path=tmp_db
    )
    assert total == 25
    assert len(rows) == 10
    assert [r["ticker"] for r in rows] == [f"T{i:02d}" for i in range(10)]

    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", page=3, page_size=10, path=tmp_db
    )
    assert total == 25
    assert len(rows) == 5  # last page has the remainder
    assert [r["ticker"] for r in rows] == [f"T{i:02d}" for i in range(20, 25)]


def test_list_decisions_summary_filters_by_ticker(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26")
    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", ticker="AAPL", path=tmp_db
    )
    assert total == 1
    assert [r["ticker"] for r in rows] == ["AAPL"]


def test_list_decisions_summary_ticker_filter_normalises_case(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", ticker="aapl", path=tmp_db
    )
    assert total == 1


def test_list_decisions_summary_filters_by_rating_and_model(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", rating="Buy", deep_model="Q")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26", rating="Hold", deep_model="Q")
    _insert(tmp_db, ticker="NVDA", trade_date="2026-05-26", rating="Buy", deep_model="K")
    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", rating="Buy", deep_model="Q", path=tmp_db
    )
    assert total == 1
    assert [r["ticker"] for r in rows] == ["AAPL"]


def test_list_decisions_summary_excludes_markdown_blobs(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    rows, _ = db.list_decisions_summary(trade_date="2026-05-26", path=tmp_db)
    keys = set(rows[0].keys())
    for col in (
        "final_decision",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_plan",
        "trader_plan",
    ):
        assert col not in keys
    # And it DOES contain what the list UI needs:
    for col in ("id", "ticker", "trade_date", "rating", "deep_model", "created_at", "has_error"):
        assert col in keys


def test_list_decisions_summary_has_error_flag(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26", error=None)
    _insert(tmp_db, ticker="BAD", trade_date="2026-05-26", error="boom")
    rows, _ = db.list_decisions_summary(trade_date="2026-05-26", path=tmp_db)
    by_ticker = {r["ticker"]: r["has_error"] for r in rows}
    assert by_ticker["AAPL"] == 0
    assert by_ticker["BAD"] == 1


def test_list_decisions_summary_scopes_to_trade_date(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25")
    rows, total = db.list_decisions_summary(trade_date="2026-05-26", path=tmp_db)
    assert total == 1
    assert rows[0]["trade_date"] == "2026-05-26"


def test_list_decisions_summary_page_beyond_range_returns_empty(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    rows, total = db.list_decisions_summary(
        trade_date="2026-05-26", page=5, page_size=10, path=tmp_db
    )
    assert total == 1
    assert rows == []
