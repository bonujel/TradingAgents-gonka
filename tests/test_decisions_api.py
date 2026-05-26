"""Integration tests for /api/decisions endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with a fresh DB and auth bypassed."""
    db_path = tmp_path / "decisions.sqlite3"
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(db_path))

    # Import AFTER setenv so db.get_db_path() picks up the override on first
    # init. The startup hook calls db.init_db() — that creates the schema in
    # our temp file.
    from app import api as api_mod
    from app import auth as auth_mod
    from app import db

    db.init_db(db_path)

    monkeypatch.setitem(
        api_mod.app.dependency_overrides,
        auth_mod.require_authenticated,
        lambda: None,
    )
    return TestClient(api_mod.app)


def _seed(**kwargs) -> None:
    """Insert one decision row via the public upsert helper.

    Callers pass fields that override the defaults below. ``deep_model`` and
    ``rating`` default to values the dashboard cares about. The row lands in
    whatever DB the ``TRADINGAGENTS_APP_DB`` env var points at — set by the
    ``api_client`` fixture, so call ``_seed`` only from tests that take
    ``api_client``.
    """
    from app import db

    db.upsert_decision(
        ticker=kwargs.get("ticker", "AAPL"),
        trade_date=kwargs.get("trade_date", "2026-05-26"),
        rating=kwargs.get("rating", "Hold"),
        final_decision=kwargs.get("final_decision", "PM verdict body"),
        reports=kwargs.get(
            "reports",
            {
                "market_report": "m",
                "sentiment_report": "s",
                "news_report": "n",
                "fundamentals_report": "f",
                "investment_plan": "ip",
                "trader_plan": "tp",
            },
        ),
        deep_model=kwargs.get("deep_model", "Qwen/Qwen3"),
        error=kwargs.get("error"),
    )


def test_list_decisions_returns_paginated_envelope(api_client):
    for i in range(3):
        _seed(ticker=f"T{i:02d}", trade_date="2026-05-26")

    resp = api_client.get("/api/decisions", params={"date": "2026-05-26"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"rows", "total", "page", "page_size"}
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["rows"]) == 3
    # Markdown blobs must not leak into the list response.
    for row in body["rows"]:
        for col in ("final_decision", "market_report", "investment_plan"):
            assert col not in row


def test_list_decisions_requires_date_param(api_client):
    resp = api_client.get("/api/decisions")
    assert resp.status_code == 422


def test_list_decisions_rejects_page_size_above_cap(api_client):
    resp = api_client.get(
        "/api/decisions", params={"date": "2026-05-26", "page_size": 999}
    )
    assert resp.status_code == 422


def test_list_decisions_rejects_zero_page(api_client):
    resp = api_client.get(
        "/api/decisions", params={"date": "2026-05-26", "page": 0}
    )
    assert resp.status_code == 422


def test_list_decisions_applies_filters_server_side(api_client):
    _seed(ticker="AAPL", trade_date="2026-05-26", rating="Buy")
    _seed(ticker="MSFT", trade_date="2026-05-26", rating="Hold")
    _seed(ticker="NVDA", trade_date="2026-05-26", rating="Buy")

    resp = api_client.get(
        "/api/decisions", params={"date": "2026-05-26", "rating": "Buy"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {r["ticker"] for r in body["rows"]} == {"AAPL", "NVDA"}


def test_list_decisions_pagination_returns_correct_slice(api_client):
    for i in range(25):
        _seed(ticker=f"T{i:02d}", trade_date="2026-05-26")

    resp = api_client.get(
        "/api/decisions",
        params={"date": "2026-05-26", "page": 2, "page_size": 10},
    )
    body = resp.json()
    assert body["total"] == 25
    assert body["page"] == 2
    assert len(body["rows"]) == 10
    assert [r["ticker"] for r in body["rows"]] == [f"T{i:02d}" for i in range(10, 20)]


def test_list_decision_models_returns_distinct_sorted(api_client):
    _seed(ticker="AAPL", trade_date="2026-05-26", deep_model="Qwen/Qwen3")
    _seed(ticker="MSFT", trade_date="2026-05-26", deep_model="moonshot/kimi")
    _seed(ticker="NVDA", trade_date="2026-05-26", deep_model="Qwen/Qwen3")

    resp = api_client.get("/api/decisions/models", params={"date": "2026-05-26"})
    assert resp.status_code == 200
    assert resp.json() == ["Qwen/Qwen3", "moonshot/kimi"]


def test_list_decision_models_requires_date(api_client):
    resp = api_client.get("/api/decisions/models")
    assert resp.status_code == 422


def test_list_decision_models_empty_when_no_rows(api_client):
    resp = api_client.get("/api/decisions/models", params={"date": "2026-05-26"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_decision_returns_full_row(api_client):
    _seed(
        ticker="AAPL",
        trade_date="2026-05-26",
        rating="Buy",
        final_decision="hold steady",
    )
    resp = api_client.get("/api/decisions/AAPL/2026-05-26")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["trade_date"] == "2026-05-26"
    assert body["rating"] == "Buy"
    assert body["final_decision"] == "hold steady"
    # Detail endpoint must still expose the markdown blobs the list omits.
    assert body["market_report"] == "m"
    assert body["investment_plan"] == "ip"


def test_get_decision_returns_404_when_missing(api_client):
    resp = api_client.get("/api/decisions/NOPE/1999-01-01")
    assert resp.status_code == 404
