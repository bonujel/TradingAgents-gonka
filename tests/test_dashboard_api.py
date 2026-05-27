"""Integration tests for /api/dashboard/* endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    """TestClient backed by a fresh per-test DB, with auth bypassed."""
    db_path = tmp_path / "decisions.sqlite3"
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(db_path))

    from app import api as api_mod
    from app import auth as auth_mod
    from app import db

    db.init_db(db_path)
    monkeypatch.setitem(
        api_mod.app.dependency_overrides,
        auth_mod.require_authenticated,
        lambda: None,
    )
    return TestClient(api_mod.app), db_path


@pytest.fixture
def api_client_no_auth_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    """TestClient with REAL auth — used to prove an endpoint is in _PUBLIC_PATHS."""
    db_path = tmp_path / "decisions.sqlite3"
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(db_path))
    from app import api as api_mod
    from app import db

    db.init_db(db_path)
    return TestClient(api_mod.app), db_path


def _seed(db_path: Path, **kwargs) -> None:
    from app import db

    db.upsert_decision(
        ticker=kwargs.get("ticker", "AAPL"),
        trade_date=kwargs.get("trade_date", "2026-05-26"),
        rating=kwargs.get("rating", "Hold"),
        final_decision=kwargs.get("final_decision", "PM verdict body"),
        reports={
            "market_report": "m",
            "sentiment_report": "s",
            "news_report": "n",
            "fundamentals_report": "f",
            "investment_plan": "ip",
            "trader_plan": "tp",
        },
        deep_model=kwargs.get("deep_model", "Qwen/Qwen3"),
        error=kwargs.get("error"),
        path=db_path,
    )


def test_dashboard_stats_returns_counts(api_client):
    client, db_path = api_client
    _seed(db_path, ticker="AAPL", trade_date="2026-05-26")
    _seed(db_path, ticker="MSFT", trade_date="2026-05-26")
    _seed(db_path, ticker="AAPL", trade_date="2026-05-25")

    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"dates_analyzed": 2, "tickers_analyzed": 2}


def test_dashboard_stats_is_public_no_token_needed(api_client_no_auth_override):
    client, _ = api_client_no_auth_override
    # No Authorization header sent. Endpoint must still respond 200, NOT 401.
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200


def test_dashboard_top_returns_ordered_cards_with_rank(api_client, monkeypatch):
    client, db_path = api_client
    # Pin the top-tickers list so the test is deterministic and offline.
    from app import api as api_mod
    monkeypatch.setattr(api_mod, "get_top_tickers", lambda n: ["AAPL", "MSFT", "NVDA"])

    _seed(db_path, ticker="AAPL", trade_date="2026-05-26", rating="Buy", deep_model="Q")
    _seed(db_path, ticker="MSFT", trade_date="2026-05-26", rating="Hold", deep_model="K")
    # NVDA intentionally absent → should yield no_decision: True card.

    resp = client.get("/api/dashboard/top")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert [c["ticker"] for c in body] == ["AAPL", "MSFT", "NVDA"]
    assert [c["market_cap_rank"] for c in body] == [1, 2, 3]
    assert body[0]["rating"] == "Buy"
    assert body[0]["deep_model"] == "Q"
    assert body[0]["has_error"] is False
    assert body[1]["rating"] == "Hold"
    assert body[2] == {"ticker": "NVDA", "market_cap_rank": 3, "no_decision": True}


def test_dashboard_top_503_when_wikipedia_fails(api_client, monkeypatch):
    client, _ = api_client
    from app import api as api_mod

    def boom(n):
        raise RuntimeError("wiki rate-limited")

    monkeypatch.setattr(api_mod, "get_top_tickers", boom)

    resp = client.get("/api/dashboard/top")
    assert resp.status_code == 503
    assert "wiki rate-limited" in resp.json()["detail"]


def test_dashboard_top_is_public_no_token_needed(api_client_no_auth_override, monkeypatch):
    client, _ = api_client_no_auth_override
    from app import api as api_mod
    monkeypatch.setattr(api_mod, "get_top_tickers", lambda n: [])
    resp = client.get("/api/dashboard/top")
    assert resp.status_code == 200
