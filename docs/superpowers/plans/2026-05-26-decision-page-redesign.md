# Decision Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the decision dashboard from "load every report for the day" to a paginated summary list + on-demand detail page.

**Architecture:** Backend `/api/decisions` becomes a paginated, filtered, summary-only endpoint (no markdown blobs) returning a `{rows, total, page, page_size}` envelope. A new `/api/decisions/models` facet endpoint feeds the model dropdown. The existing per-ticker detail endpoint stays unchanged. Frontend swaps the all-in-one list page for a summary card list driven by URL query params, with a new `/decisions/[ticker]/[trade_date]` page that fetches the full payload on demand.

**Tech Stack:** FastAPI · SQLite · pytest · Nuxt 3 · Vue 3 · Pinia · Tailwind

**Spec:** `docs/superpowers/specs/2026-05-26-decision-page-redesign-design.md`

---

## File Structure

**Backend (modified):**
- `app/db.py` — add `list_decisions_summary()` and `list_distinct_models()`. Leave existing helpers alone.
- `app/api.py` — rewrite `/api/decisions` handler. Add `/api/decisions/models`.

**Backend (new):**
- `tests/test_decisions_db.py` — unit tests for the two new db helpers.
- `tests/test_decisions_api.py` — integration tests for the rewritten + new endpoints.

**Frontend (modified):**
- `frontend/composables/useApi.ts` — new types (`DecisionSummaryRow`, `DecisionListResponse`), updated `listDecisions`, new `listDecisionModels`.
- `frontend/pages/index.vue` — rewire to server-side filter+pagination, sync state to URL query, swap card component.

**Frontend (new):**
- `frontend/components/DecisionSummaryCard.vue` — compact, clickable single-row card.
- `frontend/pages/decisions/[ticker]/[trade_date].vue` — detail page that fetches the full row.

**Frontend (unchanged but reused):**
- `frontend/components/DecisionCard.vue` — keep as-is; detail page renders it.
- `frontend/components/RatingBadge.vue`, `EmptyState.vue`, `Markdown.vue`, `CollapseSection.vue` — unchanged.

---

## Conventions Used Throughout

**Running pytest:** all backend tests assume the repo root is the cwd. The fastest invocation is the file-and-test selector pattern already used in `tests/test_tasks_registry.py`:

```bash
pytest tests/test_decisions_db.py::test_name -v
```

**DB isolation in tests:** the production DB lives at `~/.tradingagents/app/decisions.sqlite3` (`app/db.py:21`). Each test gets a fresh per-test SQLite file via the fixture below. Tests pass the path explicitly to db helpers. API tests additionally set `TRADINGAGENTS_APP_DB` so the route handlers (which call `db.<helper>()` without a path) see the same file.

**Auth in API tests:** every `/api/` route is gated by `auth.require_authenticated` (`app/api.py:48`). Tests override it the same way `tests/test_tasks_registry.py::test_api_returns_409_on_recent_duplicate` does:

```python
monkeypatch.setitem(
    app.dependency_overrides, auth_mod.require_authenticated, lambda: None
)
```

**Frontend testing:** the repo has no `vitest`/`playwright` config under `frontend/`. Frontend tasks therefore end with a **manual verification step** instead of an automated test step, per the spec's testing section.

---

## Task 1: DB helper — `list_distinct_models`

**Files:**
- Modify: `app/db.py` (add new function after `list_run_dates` at line 234)
- Create: `tests/test_decisions_db.py`

- [ ] **Step 1: Create the shared db fixture and write the first failing test**

Create `tests/test_decisions_db.py`:

```python
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
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/test_decisions_db.py::test_list_distinct_models_returns_unique_alphabetical -v
```

Expected: `AttributeError: module 'app.db' has no attribute 'list_distinct_models'`.

- [ ] **Step 3: Add the function to `app/db.py`**

Insert immediately after `list_run_dates` (currently ends at line 234):

```python
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
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/test_decisions_db.py::test_list_distinct_models_returns_unique_alphabetical -v
```

Expected: PASS.

- [ ] **Step 5: Add the date-scoping test**

Append to `tests/test_decisions_db.py`:

```python
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
```

- [ ] **Step 6: Run all tests in the file, verify they pass**

```bash
pytest tests/test_decisions_db.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add app/db.py tests/test_decisions_db.py
git commit -m "feat(db): add list_distinct_models helper for model filter dropdown"
```

---

## Task 2: DB helper — `list_decisions_summary`

**Files:**
- Modify: `app/db.py` (add new function after `list_distinct_models` from Task 1)
- Modify: `tests/test_decisions_db.py`

- [ ] **Step 1: Write the pagination test**

Append to `tests/test_decisions_db.py`:

```python
def test_list_decisions_summary_paginates(tmp_db: Path):
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
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/test_decisions_db.py::test_list_decisions_summary_paginates -v
```

Expected: `AttributeError: module 'app.db' has no attribute 'list_decisions_summary'`.

- [ ] **Step 3: Add the function to `app/db.py`**

Insert after `list_distinct_models`:

```python
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
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/test_decisions_db.py::test_list_decisions_summary_paginates -v
```

Expected: PASS.

- [ ] **Step 5: Add the filter tests**

Append to `tests/test_decisions_db.py`:

```python
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
```

- [ ] **Step 6: Run all tests, verify they pass**

```bash
pytest tests/test_decisions_db.py -v
```

Expected: 11 PASS (4 from Task 1 + 7 new).

- [ ] **Step 7: Commit**

```bash
git add app/db.py tests/test_decisions_db.py
git commit -m "feat(db): add list_decisions_summary with pagination + filters"
```

---

## Task 3: Rewrite `/api/decisions` endpoint

**Files:**
- Modify: `app/api.py` lines 193-200 (the existing `list_decisions` route handler)
- Create: `tests/test_decisions_api.py`

- [ ] **Step 1: Create the API test file with the shape test**

Create `tests/test_decisions_api.py`:

```python
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
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/test_decisions_api.py::test_list_decisions_returns_paginated_envelope -v
```

Expected: FAIL — response is a list (current shape), not a dict.

- [ ] **Step 3: Rewrite the handler in `app/api.py`**

Replace lines 193-200 (the current `list_decisions` function) with:

```python
@app.get("/api/decisions")
def list_decisions(
    date: str = Query(...),
    ticker: Optional[str] = Query(default=None),
    rating: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = db.list_decisions_summary(
        trade_date=date,
        ticker=ticker,
        rating=rating,
        deep_model=model,
        page=page,
        page_size=page_size,
    )
    return {
        "rows": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/test_decisions_api.py::test_list_decisions_returns_paginated_envelope -v
```

Expected: PASS.

- [ ] **Step 5: Add the validation tests**

Append to `tests/test_decisions_api.py`:

```python
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
```

- [ ] **Step 6: Run all tests, verify they pass**

```bash
pytest tests/test_decisions_api.py -v
```

Expected: 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api.py tests/test_decisions_api.py
git commit -m "feat(api): paginate /api/decisions and drop markdown blobs from list shape"
```

---

## Task 4: Add `/api/decisions/models` endpoint

**Files:**
- Modify: `app/api.py` (add new route immediately after the rewritten `/api/decisions` and BEFORE `/api/decisions/{ticker}/{trade_date}`)
- Modify: `tests/test_decisions_api.py`

**Why insert order matters:** FastAPI matches routes in declaration order. `/api/decisions/models` must be declared before `/api/decisions/{ticker}/{trade_date}` or the latter swallows `models` as a `ticker` segment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_decisions_api.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_decisions_api.py::test_list_decision_models_returns_distinct_sorted -v
```

Expected: 404 (route not registered).

- [ ] **Step 3: Add the route in `app/api.py`**

Insert this route IMMEDIATELY AFTER the rewritten `list_decisions` from Task 3 and IMMEDIATELY BEFORE the existing `get_decision` (`/api/decisions/{ticker}/{trade_date}`) at line 203:

```python
@app.get("/api/decisions/models")
def list_decision_models(date: str = Query(...)) -> list[str]:
    return db.list_distinct_models(trade_date=date)
```

- [ ] **Step 4: Run new tests, verify they pass**

```bash
pytest tests/test_decisions_api.py::test_list_decision_models_returns_distinct_sorted tests/test_decisions_api.py::test_list_decision_models_requires_date tests/test_decisions_api.py::test_list_decision_models_empty_when_no_rows -v
```

Expected: 3 PASS.

- [ ] **Step 5: Add tests for the unchanged detail endpoint**

The detail endpoint `/api/decisions/{ticker}/{trade_date}` is not modified by this plan, but the spec asks for coverage — and it acts as a regression guard against future route-order mistakes (e.g. if someone adds a sibling route below `/decisions/models`).

Append to `tests/test_decisions_api.py`:

```python
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
```

- [ ] **Step 6: Run all tests in the file**

```bash
pytest tests/test_decisions_api.py -v
```

Expected: 10 PASS (5 from Task 3 + 3 models + 2 detail).

- [ ] **Step 7: Commit**

```bash
git add app/api.py tests/test_decisions_api.py
git commit -m "feat(api): add /api/decisions/models facet endpoint for dropdown"
```

---

## Task 5: Update `useApi.ts` (types + new method)

**Files:**
- Modify: `frontend/composables/useApi.ts`

No frontend test infra — this task ends with `nuxi typecheck`.

- [ ] **Step 1: Replace the `DecisionRow` block with summary + full row types**

In `frontend/composables/useApi.ts`, just after the `import` line (after line 5), keep the existing `DecisionRow` interface (still used by the detail page) and ADD these new types above it:

```typescript
export interface DecisionSummaryRow {
  id: number;
  ticker: string;
  trade_date: string;
  rating: string | null;
  deep_model: string | null;
  created_at: string;
  has_error: number; // SQLite returns 0/1, treat as boolean at call sites
}

export interface DecisionListResponse {
  rows: DecisionSummaryRow[];
  total: number;
  page: number;
  page_size: number;
}
```

- [ ] **Step 2: Replace the `listDecisions` method and add `listDecisionModels`**

Find these two lines in the `return { ... }` block (around lines 210-213):

```typescript
listDecisions: (params: { date?: string; ticker?: string; limit?: number }) =>
  request<DecisionRow[]>("/api/decisions", { params }),
getDecision: (ticker: string, tradeDate: string) =>
  request<DecisionRow>(`/api/decisions/${encodeURIComponent(ticker)}/${tradeDate}`),
```

Replace `listDecisions` and ADD `listDecisionModels` (keep `getDecision`):

```typescript
listDecisions: (params: {
  date: string;
  ticker?: string;
  rating?: string;
  model?: string;
  page?: number;
  page_size?: number;
}) => request<DecisionListResponse>("/api/decisions", { params }),
listDecisionModels: (date: string) =>
  request<string[]>("/api/decisions/models", { params: { date } }),
getDecision: (ticker: string, tradeDate: string) =>
  request<DecisionRow>(`/api/decisions/${encodeURIComponent(ticker)}/${tradeDate}`),
```

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: the only errors are in `pages/index.vue` — it still uses the old `listDecisions(...)` shape. Those will be fixed in Task 7.

- [ ] **Step 4: Commit**

```bash
git add frontend/composables/useApi.ts
git commit -m "feat(api): add DecisionSummaryRow + listDecisionModels typed bindings"
```

---

## Task 6: Build `DecisionSummaryCard.vue`

**Files:**
- Create: `frontend/components/DecisionSummaryCard.vue`

- [ ] **Step 1: Create the component**

```vue
<template>
  <NuxtLink
    :to="`/decisions/${encodeURIComponent(row.ticker)}/${row.trade_date}`"
    class="block rounded-lg border border-gonka-border bg-gonka-surface px-5 py-3 transition hover:border-emerald-500/40 hover:bg-gonka-surface/80"
  >
    <div class="flex flex-wrap items-center gap-3">
      <h3 class="font-mono text-lg font-semibold tracking-tight text-white">
        {{ row.ticker }}
      </h3>
      <RatingBadge :rating="row.rating" />
      <span
        v-if="row.has_error"
        class="inline-flex items-center rounded-md bg-red-500/15 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-red-300 ring-1 ring-inset ring-red-500/30"
      >
        errored
      </span>
      <span class="chip">{{ row.trade_date }}</span>
      <span class="chip">{{ shortModel }}</span>
      <span class="ml-auto text-xs text-gonka-muted">
        stored {{ shortTimestamp(row.created_at) }}
      </span>
    </div>
  </NuxtLink>
</template>

<script setup lang="ts">
import type { DecisionSummaryRow } from "~/composables/useApi";

const props = defineProps<{ row: DecisionSummaryRow }>();

const shortModel = computed(() => {
  const m = props.row.deep_model;
  if (!m) return "—";
  const tail = m.split("/").pop() || m;
  return tail.length > 28 ? tail.slice(0, 28) + "…" : tail;
});

function shortTimestamp(iso: string): string {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 19);
}
</script>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: no new errors from this file. (The index.vue errors from Task 5 still remain — Task 7 fixes them.)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/DecisionSummaryCard.vue
git commit -m "feat(ui): add DecisionSummaryCard for the compact list view"
```

---

## Task 7: Rewire `frontend/pages/index.vue` to server-side mode

**Files:**
- Modify: `frontend/pages/index.vue` (full rewrite of script + template list section)

- [ ] **Step 1: Replace the file contents**

Overwrite `frontend/pages/index.vue` entirely with:

```vue
<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight text-white">Decisions</h2>
        <p class="mt-1 text-sm text-gonka-muted">
          Latest agent verdicts persisted from completed runs.
        </p>
      </div>
      <button class="btn" :disabled="loading" @click="reloadAll">
        <span v-if="loading">Refreshing…</span>
        <span v-else>Refresh</span>
      </button>
    </header>

    <section class="grid gap-4 sm:grid-cols-3">
      <MetricCard label="Trade dates" :value="dates.length" hint="Calendar days on record" />
      <MetricCard label="Matching" :value="total" hint="Rows for current filter" />
      <MetricCard label="Page size" :value="pageSize" hint="Rows per request" />
    </section>

    <section class="card space-y-4 p-5">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label class="label mb-1">Trade date</label>
          <select v-model="selectedDate" class="input" :disabled="!dates.length">
            <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <div>
          <label class="label mb-1">Ticker</label>
          <input
            v-model="tickerFilter"
            type="text"
            placeholder="e.g. NVDA"
            class="input uppercase"
            @keyup.enter="page = 1"
          />
        </div>
        <div>
          <label class="label mb-1">Rating</label>
          <select v-model="selectedRating" class="input">
            <option value="">All ratings</option>
            <option v-for="r in RATING_ORDER" :key="r" :value="r">{{ r }}</option>
          </select>
        </div>
        <div>
          <label class="label mb-1">Model</label>
          <select v-model="selectedModel" class="input" :disabled="!availableModels.length">
            <option value="">All models</option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="flex items-end gap-2">
          <div class="flex-1">
            <label class="label mb-1">Rows on page</label>
            <select v-model.number="pageSize" class="input">
              <option v-for="n in [10, 20, 50, 100, 200]" :key="n" :value="n">{{ n }}</option>
            </select>
          </div>
          <button
            v-if="tickerFilter || selectedRating || selectedModel"
            class="btn btn-ghost"
            type="button"
            @click="
              tickerFilter = '';
              selectedRating = '';
              selectedModel = '';
            "
          >
            Clear
          </button>
        </div>
      </div>
    </section>

    <section v-if="loadError" class="card border border-red-500/40 bg-red-500/5 p-5">
      <div class="mb-2 text-xs font-semibold uppercase tracking-wider text-red-300">
        Failed to load decisions
      </div>
      <p class="text-sm text-red-200">{{ loadError }}</p>
      <button class="btn btn-ghost mt-3" type="button" @click="loadRows">Retry</button>
    </section>

    <section v-else-if="!dates.length && !loading">
      <EmptyState
        title="No decisions yet"
        description="Open the Tasks page to launch a run; the first PM verdict will show up here."
      >
        <template #actions>
          <NuxtLink to="/tasks" class="btn btn-primary">Go to Tasks</NuxtLink>
        </template>
      </EmptyState>
    </section>

    <section v-else-if="!rows.length && !loading">
      <EmptyState title="No matching decisions" description="Try a different ticker or date." />
    </section>

    <section v-else class="space-y-3">
      <DecisionSummaryCard
        v-for="row in rows"
        :key="`${row.ticker}-${row.trade_date}`"
        :row="row"
      />
      <div
        v-if="totalPages > 1"
        class="flex items-center justify-between rounded-lg border border-gonka-border bg-gonka-surface px-4 py-3 text-xs text-gonka-muted"
      >
        <span>
          Showing rows
          {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, total) }}
          of {{ total }}.
        </span>
        <div class="flex items-center gap-2">
          <button
            class="btn btn-ghost"
            :disabled="page <= 1"
            type="button"
            @click="page = Math.max(1, page - 1)"
          >
            ← Prev
          </button>
          <span class="font-mono">{{ page }} / {{ totalPages }}</span>
          <button
            class="btn btn-ghost"
            :disabled="page >= totalPages"
            type="button"
            @click="page = Math.min(totalPages, page + 1)"
          >
            Next →
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { DecisionSummaryRow } from "~/composables/useApi";

const api = useApi();
const route = useRoute();
const router = useRouter();

const RATING_ORDER = ["Buy", "Overweight", "Hold", "Underweight", "Sell"];

// ─── URL ⇄ state ─────────────────────────────────────────────────────────
//
// The list page persists its full view (date + filters + page) in the URL
// query string so:
//   * browser back from the detail page restores the same view
//   * a shared URL reproduces the same view elsewhere
//   * refresh keeps state
//
// Reads happen once on mount; writes flow through `pushQuery()` whenever a
// reactive piece of state changes.

function readNumber(value: unknown, fallback: number): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const dates = ref<string[]>([]);
const selectedDate = ref<string>(readString(route.query.date));
const tickerFilter = ref<string>(readString(route.query.ticker));
const selectedRating = ref<string>(readString(route.query.rating));
const selectedModel = ref<string>(readString(route.query.model));
const pageSize = ref<number>(readNumber(route.query.page_size, 20));
const page = ref<number>(readNumber(route.query.page, 1));

const rows = ref<DecisionSummaryRow[]>([]);
const total = ref(0);
const availableModels = ref<string[]>([]);
const loading = ref(false);
const loadError = ref<string | null>(null);

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));

function pushQuery() {
  const q: Record<string, string> = {};
  if (selectedDate.value) q.date = selectedDate.value;
  if (tickerFilter.value) q.ticker = tickerFilter.value.toUpperCase();
  if (selectedRating.value) q.rating = selectedRating.value;
  if (selectedModel.value) q.model = selectedModel.value;
  if (page.value !== 1) q.page = String(page.value);
  if (pageSize.value !== 20) q.page_size = String(pageSize.value);
  router.replace({ query: q });
}

async function loadDates() {
  try {
    dates.value = await api.listDates();
    if (dates.value.length && !selectedDate.value) {
      selectedDate.value = dates.value[0];
    }
  } catch {
    dates.value = [];
  }
}

async function loadModels() {
  if (!selectedDate.value) {
    availableModels.value = [];
    return;
  }
  try {
    availableModels.value = await api.listDecisionModels(selectedDate.value);
  } catch {
    availableModels.value = [];
  }
}

async function loadRows() {
  if (!selectedDate.value) {
    rows.value = [];
    total.value = 0;
    return;
  }
  loading.value = true;
  loadError.value = null;
  try {
    const ticker = tickerFilter.value.trim().toUpperCase() || undefined;
    const body = await api.listDecisions({
      date: selectedDate.value,
      ticker,
      rating: selectedRating.value || undefined,
      model: selectedModel.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    });
    rows.value = body.rows;
    total.value = body.total;
  } catch (err: unknown) {
    const e = err as { data?: { detail?: string }; message?: string };
    loadError.value = e?.data?.detail || e?.message || "Failed to load decisions.";
    rows.value = [];
    total.value = 0;
  } finally {
    loading.value = false;
  }
}

async function reloadAll() {
  await loadDates();
  await loadModels();
  await loadRows();
}

// ─── Watchers ────────────────────────────────────────────────────────────
// Any change to the filter set should snap back to page 1; changing the
// page itself obviously does not.

watch([tickerFilter, selectedRating, selectedModel, pageSize], () => {
  if (page.value !== 1) page.value = 1;
  pushQuery();
  loadRows();
});

watch(page, () => {
  pushQuery();
  loadRows();
});

watch(selectedDate, async () => {
  page.value = 1;
  pushQuery();
  await loadModels();
  await loadRows();
});

// If the selected model is no longer present after the date changed, drop
// it so the user does not see an empty result with no visual cue.
watch(availableModels, (models) => {
  if (selectedModel.value && !models.includes(selectedModel.value)) {
    selectedModel.value = "";
  }
});

onMounted(reloadAll);
</script>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: clean (no errors).

- [ ] **Step 3: Build to catch any template / config issue**

```bash
cd frontend && npx nuxi build
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/index.vue
git commit -m "feat(ui): rewire decisions list to server-side filter + pagination"
```

---

## Task 8: Build the detail page

**Files:**
- Create: `frontend/pages/decisions/[ticker]/[trade_date].vue`

- [ ] **Step 1: Create the page**

```vue
<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <button class="btn btn-ghost" type="button" @click="goBack">← Back</button>
      <NuxtLink to="/" class="text-xs text-gonka-muted hover:text-gonka-text">
        All decisions
      </NuxtLink>
    </div>

    <section v-if="loading" class="card p-10 text-center text-sm text-gonka-muted">
      Loading decision…
    </section>

    <section v-else-if="notFound" class="card p-10 text-center">
      <h3 class="text-sm font-semibold text-gonka-text">Decision not found</h3>
      <p class="mt-1 text-xs text-gonka-muted">
        No row stored for <span class="font-mono">{{ ticker }}</span> on
        <span class="font-mono">{{ tradeDate }}</span>.
      </p>
      <NuxtLink to="/" class="btn btn-primary mt-4 inline-flex">Back to list</NuxtLink>
    </section>

    <section v-else-if="error" class="card border border-red-500/40 bg-red-500/5 p-5">
      <div class="mb-2 text-xs font-semibold uppercase tracking-wider text-red-300">
        Failed to load decision
      </div>
      <p class="text-sm text-red-200">{{ error }}</p>
      <button class="btn btn-ghost mt-3" type="button" @click="load">Retry</button>
    </section>

    <DecisionCard v-else-if="row" :row="row" />
  </div>
</template>

<script setup lang="ts">
import type { DecisionRow } from "~/composables/useApi";

const api = useApi();
const route = useRoute();
const router = useRouter();

const ticker = computed(() => String(route.params.ticker || "").toUpperCase());
const tradeDate = computed(() => String(route.params.trade_date || ""));

const row = ref<DecisionRow | null>(null);
const loading = ref(false);
const notFound = ref(false);
const error = ref<string | null>(null);

async function load() {
  if (!ticker.value || !tradeDate.value) return;
  loading.value = true;
  notFound.value = false;
  error.value = null;
  try {
    row.value = await api.getDecision(ticker.value, tradeDate.value);
  } catch (err: unknown) {
    const e = err as {
      response?: { status?: number };
      statusCode?: number;
      data?: { detail?: string };
      message?: string;
    };
    const status = e?.response?.status ?? e?.statusCode;
    if (status === 404) {
      notFound.value = true;
      row.value = null;
    } else {
      error.value = e?.data?.detail || e?.message || "Failed to load decision.";
      row.value = null;
    }
  } finally {
    loading.value = false;
  }
}

function goBack() {
  // window.history.length > 1 means we have a real back stack to use.
  // Otherwise (deep link / refresh from a shared URL) fall back to the list.
  if (typeof window !== "undefined" && window.history.length > 1) {
    router.back();
  } else {
    router.push("/");
  }
}

onMounted(load);
watch([ticker, tradeDate], load);
</script>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: clean.

- [ ] **Step 3: Build**

```bash
cd frontend && npx nuxi build
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/decisions/[ticker]/[trade_date].vue
git commit -m "feat(ui): add decision detail page with on-demand fetch"
```

---

## Task 9: End-to-end verification

No code changes. This task confirms the whole feature works in the browser before declaring done.

- [ ] **Step 1: Run the full backend test suite**

```bash
pytest tests/test_decisions_db.py tests/test_decisions_api.py -v
```

Expected: all PASS (11 db + 10 api = 21 tests).

- [ ] **Step 2: Start the app**

```bash
./start.sh
```

Wait for the frontend to be reachable (default `http://127.0.0.1:3000`).

- [ ] **Step 3: Walk the checklist**

For each item below, observe the behaviour and tick it off. If any fails, stop and fix it before continuing.

- [ ] List page loads at `/` with default date populated and rows visible
- [ ] Changing the date triggers a fresh fetch; model dropdown refreshes
- [ ] Typing a ticker (e.g. `AAPL`) and applying filters narrows the list; "Matching" count updates
- [ ] Selecting a rating (e.g. `Buy`) narrows the list; total page count adjusts
- [ ] Pagination prev / next works; page indicator updates; URL `?page=N` updates
- [ ] Refreshing a URL with `?date=...&page=2&rating=Buy` restores the same view
- [ ] Clicking a summary card navigates to `/decisions/<ticker>/<date>`
- [ ] Detail page shows the full PM verdict, trader plan, investment plan, and analyst tabs
- [ ] Browser ← back returns to the list with previous filters / page restored
- [ ] Direct visit to `/decisions/AAPL/1999-01-01` (non-existent) shows the 404 view
- [ ] If an errored decision exists, the summary card shows the red "errored" chip and the detail page renders the error block

- [ ] **Step 4: Final summary commit (optional)**

Only if any small UI polish was made during verification:

```bash
git add -A
git commit -m "chore(ui): polish decision page after end-to-end verification"
```

Otherwise this task has no commit — the previous task commits already cover the work.
