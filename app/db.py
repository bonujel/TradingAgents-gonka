"""SQLite persistence for daily TradingAgents decisions.

The schema is intentionally narrow: one row per (ticker, trade_date) keyed run.
Re-running the same (ticker, trade_date) replaces the existing row so a
mid-day retry doesn't accumulate duplicates. Full agent reports are kept in a
separate column rather than a side file so the dashboard can render everything
without touching the filesystem.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


_DEFAULT_DB_PATH = Path.home() / ".tradingagents" / "app" / "decisions.sqlite3"


def get_db_path() -> Path:
    """Resolve the SQLite path, honoring ``TRADINGAGENTS_APP_DB`` when set."""
    override = os.environ.get("TRADINGAGENTS_APP_DB")
    if override:
        return Path(override)
    return _DEFAULT_DB_PATH


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect(path: Optional[Path] = None):
    """Open a SQLite connection with foreign keys + row factory.

    Used as a context manager so the connection is always closed and the
    transaction committed (or rolled back) deterministically.
    """
    db_path = path or get_db_path()
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    trade_date      TEXT    NOT NULL,
    rating          TEXT,
    final_decision  TEXT,
    market_report   TEXT,
    sentiment_report TEXT,
    news_report     TEXT,
    fundamentals_report TEXT,
    investment_plan TEXT,
    trader_plan     TEXT,
    model_provider  TEXT,
    deep_model      TEXT,
    quick_model     TEXT,
    error           TEXT,
    created_at      TEXT    NOT NULL,
    UNIQUE(ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(trade_date);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker);

CREATE TABLE IF NOT EXISTS run_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    tickers      TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);
"""


def init_db(path: Optional[Path] = None) -> Path:
    """Create the schema if it does not exist; return the resolved DB path."""
    db_path = path or get_db_path()
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
    return db_path


def upsert_decision(
    *,
    ticker: str,
    trade_date: str,
    rating: Optional[str],
    final_decision: Optional[str],
    reports: dict[str, Any],
    model_provider: Optional[str] = None,
    deep_model: Optional[str] = None,
    quick_model: Optional[str] = None,
    error: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    """Insert or replace the decision row for ``(ticker, trade_date)``.

    ``reports`` is a flat dict of the per-analyst markdown blocks; only the
    keys the dashboard renders are pulled out. Anything extra is preserved by
    serialising the leftover keys into ``notes`` would just bloat the row, so
    we drop them — the JSON state log on disk is still the source of truth for
    full traces.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO decisions (
                ticker, trade_date, rating, final_decision,
                market_report, sentiment_report, news_report, fundamentals_report,
                investment_plan, trader_plan,
                model_provider, deep_model, quick_model, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date) DO UPDATE SET
                rating              = excluded.rating,
                final_decision      = excluded.final_decision,
                market_report       = excluded.market_report,
                sentiment_report    = excluded.sentiment_report,
                news_report         = excluded.news_report,
                fundamentals_report = excluded.fundamentals_report,
                investment_plan     = excluded.investment_plan,
                trader_plan         = excluded.trader_plan,
                model_provider      = excluded.model_provider,
                deep_model          = excluded.deep_model,
                quick_model         = excluded.quick_model,
                error               = excluded.error,
                created_at          = excluded.created_at
            """,
            (
                ticker,
                trade_date,
                rating,
                final_decision,
                reports.get("market_report"),
                reports.get("sentiment_report"),
                reports.get("news_report"),
                reports.get("fundamentals_report"),
                reports.get("investment_plan"),
                reports.get("trader_plan"),
                model_provider,
                deep_model,
                quick_model,
                error,
                now,
            ),
        )


def start_run(*, run_date: str, tickers: Iterable[str], path: Optional[Path] = None) -> int:
    started = datetime.utcnow().isoformat(timespec="seconds")
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO run_log(run_date, started_at, tickers) VALUES (?, ?, ?)",
            (run_date, started, json.dumps(list(tickers))),
        )
        return int(cur.lastrowid)


def finish_run(
    *,
    run_id: int,
    success: int,
    failure: int,
    notes: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    finished = datetime.utcnow().isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.execute(
            """
            UPDATE run_log
               SET finished_at = ?, success_count = ?, failure_count = ?, notes = ?
             WHERE id = ?
            """,
            (finished, success, failure, notes, run_id),
        )


def list_decisions(
    *,
    trade_date: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 500,
    path: Optional[Path] = None,
) -> list[sqlite3.Row]:
    clauses, params = [], []
    if trade_date:
        clauses.append("trade_date = ?")
        params.append(trade_date)
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect(path) as conn:
        return list(
            conn.execute(
                f"""
                SELECT * FROM decisions
                {where}
                ORDER BY trade_date DESC, ticker ASC
                LIMIT ?
                """,
                params,
            )
        )


def list_run_dates(*, path: Optional[Path] = None) -> list[str]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM decisions ORDER BY trade_date DESC"
        ).fetchall()
        return [r["trade_date"] for r in rows]


def list_distinct_models(
    *, trade_date: str, path: Optional[Path] = None
) -> list[str]:
    """Distinct ``deep_model`` values recorded for ``trade_date``.

    Used by the dashboard's model-filter dropdown so the menu reflects what
    actually ran that day rather than every model ever used.
    """
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT deep_model
              FROM decisions
             WHERE trade_date = ? AND deep_model IS NOT NULL
             ORDER BY deep_model ASC
            """,
            (trade_date,),
        ).fetchall()
        return [r["deep_model"] for r in rows]


def list_decisions_summary(
    *,
    trade_date: str,
    ticker: Optional[str] = None,
    rating: Optional[str] = None,
    deep_model: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    path: Optional[Path] = None,
) -> tuple[list[sqlite3.Row], int]:
    """Return ``(rows, total)`` for the decision dashboard list view.

    Rows exclude the markdown blob columns (``final_decision``, the four
    analyst reports, ``investment_plan``, ``trader_plan``) — those are
    fetched on demand by the detail endpoint. ``total`` is the unpaginated
    count, used by the UI to render page numbers.
    """
    clauses: list[str] = ["trade_date = ?"]
    params: list[Any] = [trade_date]
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if rating:
        clauses.append("rating = ?")
        params.append(rating)
    if deep_model:
        clauses.append("deep_model = ?")
        params.append(deep_model)
    where = " AND ".join(clauses)

    offset = (page - 1) * page_size
    with connect(path) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM decisions WHERE {where}", params
        ).fetchone()
        total = int(total_row["n"])
        rows = list(
            conn.execute(
                f"""
                SELECT id, ticker, trade_date, rating, deep_model, created_at,
                       (error IS NOT NULL) AS has_error
                  FROM decisions
                 WHERE {where}
                 ORDER BY trade_date DESC, ticker ASC
                 LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            )
        )
    return rows, total


def list_runs(*, limit: int = 50, path: Optional[Path] = None) -> list[sqlite3.Row]:
    with connect(path) as conn:
        return list(
            conn.execute(
                "SELECT * FROM run_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        )
