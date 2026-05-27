# Public Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public, sci-fi-styled `/dashboard` page so anonymous visitors see live project state (analyzed days, tickers covered, SP top-20 verdicts), with an in-place login modal gating every other tab.

**Architecture:** Three new public backend endpoints serve dashboard data without auth (`stats`, `top`). The frontend route guard sends anonymous traffic to `/dashboard` and opens a global Pinia-controlled login modal for any other tab. Visual layer: rotating conic-gradient borders, count-up digits, hex-grid backdrop, drifting particles — pure CSS + a tiny RAF composable.

**Tech Stack:** FastAPI · SQLite · pytest · Nuxt 3 · Vue 3 · Pinia · Tailwind

**Spec:** `docs/superpowers/specs/2026-05-27-public-dashboard-design.md`

**Commit policy:** Do NOT commit between tasks. Leave every change in the working tree. The orchestrator will commit at the end after reviewing the full diff.

---

## File Structure

**Backend (modified):**
- `app/db.py` — 3 new helpers (`count_distinct_dates`, `count_distinct_tickers`, `get_latest_decision_per_ticker`)
- `app/api.py` — 2 new public routes + 1 private helper
- `app/auth.py` — extend `_PUBLIC_PATHS` (4-line change)

**Backend (new):**
- `tests/test_dashboard_db.py` — unit tests for the 3 helpers
- `tests/test_dashboard_api.py` — integration tests for the 2 endpoints + public-access proof

**Frontend (modified):**
- `frontend/middleware/auth.global.ts` — rewrite for public dashboard + login-modal redirects
- `frontend/composables/useApi.ts` — types, new methods, 401-on-public-path safety
- `frontend/components/AppSidebar.vue` — anonymous branch with locked tabs
- `frontend/components/AppTopbar.vue` — anonymous polish (skip mode/model/workers chips)
- `frontend/layouts/default.vue` — gate `infoStore.refresh()` on auth
- `frontend/app.vue` — mount `<LoginModal />` globally

**Frontend (new):**
- `frontend/stores/loginModal.ts`
- `frontend/composables/useCountUp.ts`
- `frontend/components/LoginModal.vue`
- `frontend/components/DashboardBackground.vue`
- `frontend/components/DashboardMetricCard.vue`
- `frontend/components/DashboardTickerCard.vue`
- `frontend/pages/dashboard.vue`

---

## Conventions

**pytest:** All backend tests assume cwd = repo root.

```bash
pytest tests/test_dashboard_db.py -v
pytest tests/test_dashboard_api.py -v
```

**DB isolation:** Per-test SQLite file via `tmp_path` + `db.init_db(path)`. For API tests, also set env `TRADINGAGENTS_APP_DB` so route handlers (which call `db.<helper>()` without a path) see the same file.

**Auth bypass in tests:** Override the app-level dependency with monkeypatch (pattern used in `tests/test_decisions_api.py`):

```python
monkeypatch.setitem(
    api_mod.app.dependency_overrides,
    auth_mod.require_authenticated,
    lambda: None,
)
```

For tests proving an endpoint is PUBLIC (no token), do NOT install that override — the real `require_authenticated` should let the request through because the path is in `_PUBLIC_PATHS`.

**Frontend tooling:**

```bash
cd frontend && npx nuxi typecheck   # vue-tsc; pre-existing errors in ActiveRunRow / AppSidebar / useApi remain — your task is "no NEW errors from your file"
cd frontend && npx nuxi build       # full Nitro build, ~30s
```

The repo has no frontend test framework; frontend tasks end with `typecheck` + `build` and (where useful) a quick `curl` against the dev server.

---

## Task 1: DB helpers — `count_distinct_dates` and `count_distinct_tickers`

**Files:**
- Modify: `app/db.py` (add 2 functions after `list_run_dates`)
- Create: `tests/test_dashboard_db.py`

- [ ] **Step 1: Create test file with the first failing test**

Create `tests/test_dashboard_db.py`:

```python
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
```

- [ ] **Step 2: Run, verify both fail**

```bash
pytest tests/test_dashboard_db.py -v
```

Expected: `AttributeError: module 'app.db' has no attribute 'count_distinct_dates'` on both.

- [ ] **Step 3: Add `count_distinct_dates` to `app/db.py`**

Insert after `list_run_dates`:

```python
def count_distinct_dates(*, path: Optional[Path] = None) -> int:
    """Total number of distinct trade_dates with at least one decision."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT trade_date) AS n FROM decisions"
        ).fetchone()
        return int(row["n"])
```

- [ ] **Step 4: Run, verify the 2 count_distinct_dates tests pass**

```bash
pytest tests/test_dashboard_db.py -v -k count_distinct_dates
```

Expected: 2 PASS.

- [ ] **Step 5: Add ticker-count tests + implementation**

Append to `tests/test_dashboard_db.py`:

```python
def test_count_distinct_tickers_empty(tmp_db: Path):
    assert db.count_distinct_tickers(path=tmp_db) == 0


def test_count_distinct_tickers_counts_unique_tickers(tmp_db: Path):
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-26")
    _insert(tmp_db, ticker="AAPL", trade_date="2026-05-25")
    _insert(tmp_db, ticker="MSFT", trade_date="2026-05-26")
    assert db.count_distinct_tickers(path=tmp_db) == 2
```

Append to `app/db.py` (just below `count_distinct_dates`):

```python
def count_distinct_tickers(*, path: Optional[Path] = None) -> int:
    """Total number of distinct tickers ever analyzed."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT ticker) AS n FROM decisions"
        ).fetchone()
        return int(row["n"])
```

- [ ] **Step 6: Run all 4 tests, verify all pass**

```bash
pytest tests/test_dashboard_db.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Do NOT commit.** Leave changes in the working tree.

---

## Task 2: DB helper — `get_latest_decision_per_ticker`

**Files:**
- Modify: `app/db.py` (add function after `count_distinct_tickers` from Task 1)
- Modify: `tests/test_dashboard_db.py`

- [ ] **Step 1: Write the "happy path" test**

Append to `tests/test_dashboard_db.py`:

```python
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
```

- [ ] **Step 2: Run, verify it fails**

```bash
pytest tests/test_dashboard_db.py::test_get_latest_decision_per_ticker_returns_latest_per_ticker -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement in `app/db.py`**

Append after `count_distinct_tickers`:

```python
def get_latest_decision_per_ticker(
    *, tickers: list[str], path: Optional[Path] = None
) -> dict[str, sqlite3.Row]:
    """For each ticker, return its most recent decision (summary columns only).

    Tickers without any stored decision are simply absent from the returned
    dict; the caller fills the gap with a placeholder. One SQL round-trip;
    avoids N+1 by joining each ticker to its MAX(trade_date) in a subquery.
    """
    if not tickers:
        return {}
    upper = [t.upper() for t in tickers]
    placeholders = ",".join("?" * len(upper))
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, ticker, trade_date, rating, deep_model, created_at,
                   (error IS NOT NULL) AS has_error
              FROM decisions
             WHERE ticker IN ({placeholders})
               AND (ticker, trade_date) IN (
                   SELECT ticker, MAX(trade_date)
                     FROM decisions
                    WHERE ticker IN ({placeholders})
                    GROUP BY ticker
               )
            """,
            [*upper, *upper],
        ).fetchall()
    return {r["ticker"]: r for r in rows}
```

- [ ] **Step 4: Run, verify it passes**

```bash
pytest tests/test_dashboard_db.py::test_get_latest_decision_per_ticker_returns_latest_per_ticker -v
```

Expected: PASS.

- [ ] **Step 5: Add edge-case tests**

Append to `tests/test_dashboard_db.py`:

```python
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
```

- [ ] **Step 6: Run all dashboard_db tests, verify all pass**

```bash
pytest tests/test_dashboard_db.py -v
```

Expected: 8 PASS.

- [ ] **Step 7: Do NOT commit.**

---

## Task 3: API endpoints + public-path extension + tests

**Files:**
- Modify: `app/auth.py` (extend `_PUBLIC_PATHS`)
- Modify: `app/api.py` (add 2 routes + 1 helper)
- Create: `tests/test_dashboard_api.py`

- [ ] **Step 1: Create test file with the stats endpoint test**

Create `tests/test_dashboard_api.py`:

```python
"""Integration tests for /api/dashboard/* endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    return TestClient(api_mod.app)


@pytest.fixture
def api_client_no_auth_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with REAL auth — used to prove an endpoint is in _PUBLIC_PATHS."""
    db_path = tmp_path / "decisions.sqlite3"
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(db_path))
    from app import api as api_mod
    from app import db

    db.init_db(db_path)
    return TestClient(api_mod.app)


def _seed(**kwargs) -> None:
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
    )


def test_dashboard_stats_returns_counts(api_client):
    _seed(ticker="AAPL", trade_date="2026-05-26")
    _seed(ticker="MSFT", trade_date="2026-05-26")
    _seed(ticker="AAPL", trade_date="2026-05-25")

    resp = api_client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"dates_analyzed": 2, "tickers_analyzed": 2}
```

- [ ] **Step 2: Run, verify it fails**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_stats_returns_counts -v
```

Expected: 404 (route does not exist).

- [ ] **Step 3: Add the stats endpoint to `app/api.py`**

Find the section starting at line 181 (`# ─── Decisions ──`). Add a NEW section above it (or below it — doesn't matter for FastAPI):

```python
# ─── Dashboard (public) ───────────────────────────────────────────────────


@app.get("/api/dashboard/stats")
def dashboard_stats() -> dict[str, int]:
    return {
        "dates_analyzed": db.count_distinct_dates(),
        "tickers_analyzed": db.count_distinct_tickers(),
    }
```

- [ ] **Step 4: Run, verify it passes**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_stats_returns_counts -v
```

Expected: PASS.

- [ ] **Step 5: Add the public-access test for stats**

Append to `tests/test_dashboard_api.py`:

```python
def test_dashboard_stats_is_public_no_token_needed(api_client_no_auth_override):
    # No Authorization header sent. Endpoint must still respond 200, NOT 401.
    resp = api_client_no_auth_override.get("/api/dashboard/stats")
    assert resp.status_code == 200
```

- [ ] **Step 6: Run, verify it FAILS with 401 (path not yet in _PUBLIC_PATHS)**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_stats_is_public_no_token_needed -v
```

Expected: FAIL with `assert 401 == 200`.

- [ ] **Step 7: Extend `_PUBLIC_PATHS` in `app/auth.py`**

Replace the existing `_PUBLIC_PATHS` definition (currently `frozenset({"/api/health", "/api/auth/login"})`):

```python
_PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/dashboard/stats",
    "/api/dashboard/top",
})
```

- [ ] **Step 8: Re-run, verify it passes**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_stats_is_public_no_token_needed -v
```

Expected: PASS.

- [ ] **Step 9: Add the top endpoint test (with get_top_tickers monkeypatched)**

Append to `tests/test_dashboard_api.py`:

```python
def test_dashboard_top_returns_ordered_cards_with_rank(api_client, monkeypatch):
    # Pin the top-tickers list so the test is deterministic and offline.
    from app import api as api_mod
    monkeypatch.setattr(api_mod, "get_top_tickers", lambda n: ["AAPL", "MSFT", "NVDA"])

    _seed(ticker="AAPL", trade_date="2026-05-26", rating="Buy", deep_model="Q")
    _seed(ticker="MSFT", trade_date="2026-05-26", rating="Hold", deep_model="K")
    # NVDA intentionally absent → should yield no_decision: True card.

    resp = api_client.get("/api/dashboard/top")
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
```

- [ ] **Step 10: Run, verify it fails**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_top_returns_ordered_cards_with_rank -v
```

Expected: 404.

- [ ] **Step 11: Add the top endpoint + helper to `app/api.py`**

Append to the dashboard section from Step 3:

```python
@app.get("/api/dashboard/top")
def dashboard_top() -> list[dict[str, Any]]:
    try:
        tickers = get_top_tickers(n=20)
    except Exception as exc:
        # get_top_tickers hits Wikipedia. Surface a 503 so the frontend
        # can degrade. Stats endpoint is unaffected.
        raise HTTPException(status_code=503, detail=f"top tickers unavailable: {exc}") from exc
    latest = db.get_latest_decision_per_ticker(tickers=tickers)
    return [
        _to_dashboard_card(t, rank, latest.get(t.upper()))
        for rank, t in enumerate(tickers, start=1)
    ]


def _to_dashboard_card(
    ticker: str, rank: int, row
) -> dict[str, Any]:
    if row is None:
        return {"ticker": ticker, "market_cap_rank": rank, "no_decision": True}
    return {
        "ticker": ticker,
        "market_cap_rank": rank,
        "rating": row["rating"],
        "deep_model": row["deep_model"],
        "trade_date": row["trade_date"],
        "created_at": row["created_at"],
        "has_error": bool(row["has_error"]),
    }
```

- [ ] **Step 12: Run, verify it passes**

```bash
pytest tests/test_dashboard_api.py::test_dashboard_top_returns_ordered_cards_with_rank -v
```

Expected: PASS.

- [ ] **Step 13: Add the remaining top tests**

Append to `tests/test_dashboard_api.py`:

```python
def test_dashboard_top_503_when_wikipedia_fails(api_client, monkeypatch):
    from app import api as api_mod

    def boom(n):
        raise RuntimeError("wiki rate-limited")

    monkeypatch.setattr(api_mod, "get_top_tickers", boom)

    resp = api_client.get("/api/dashboard/top")
    assert resp.status_code == 503
    assert "wiki rate-limited" in resp.json()["detail"]


def test_dashboard_top_is_public_no_token_needed(api_client_no_auth_override, monkeypatch):
    from app import api as api_mod
    monkeypatch.setattr(api_mod, "get_top_tickers", lambda n: [])
    resp = api_client_no_auth_override.get("/api/dashboard/top")
    assert resp.status_code == 200
```

- [ ] **Step 14: Run the entire dashboard_api file, verify all pass**

```bash
pytest tests/test_dashboard_api.py -v
```

Expected: 5 PASS.

- [ ] **Step 15: Do NOT commit.**

---

## Task 4: `useApi.ts` — types, methods, 401-safety for public path

**Files:**
- Modify: `frontend/composables/useApi.ts`

- [ ] **Step 1: Add the two interfaces**

In `frontend/composables/useApi.ts`, find the existing `DecisionSummaryRow` / `DecisionListResponse` block (~line 7) and ADD just below them, before `interface ActiveTask`:

```typescript
export interface DashboardStats {
  dates_analyzed: number;
  tickers_analyzed: number;
}

export interface DashboardCard {
  ticker: string;
  market_cap_rank: number;
  rating?: string | null;
  deep_model?: string | null;
  trade_date?: string;
  created_at?: string;
  has_error?: boolean;
  no_decision?: boolean;
}
```

- [ ] **Step 2: Adjust the 401 redirect to skip public paths**

Find the existing block in `request()`:

```typescript
if (status === 401 && path !== "/api/auth/login") {
  auth.clear();
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    navigateTo("/login");
  }
}
```

Replace with:

```typescript
// /dashboard is publicly viewable; if anything 401s while we're sitting
// there (e.g. someone hits Refresh after token expiry), do not yank the
// visitor away from the public page.
const PUBLIC_PAGE_PATHS = new Set(["/login", "/dashboard"]);
if (status === 401 && path !== "/api/auth/login") {
  auth.clear();
  if (
    typeof window !== "undefined" &&
    !PUBLIC_PAGE_PATHS.has(window.location.pathname)
  ) {
    navigateTo("/login");
  }
}
```

- [ ] **Step 3: Add the two methods**

In the `return { ... }` block, add (right after `getDecision`):

```typescript
getDashboardStats: () => request<DashboardStats>("/api/dashboard/stats"),
getDashboardTop: () => request<DashboardCard[]>("/api/dashboard/top"),
```

- [ ] **Step 4: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: no NEW errors from `useApi.ts`. Pre-existing errors in `ActiveRunRow.vue`, `AppSidebar.vue`, and the `useApi.ts:$fetch` line itself remain — that's NOT your concern. The only new file you must keep clean is the two interfaces and two methods you added.

- [ ] **Step 5: Do NOT commit.**

---

## Task 5: `useCountUp` composable

**Files:**
- Create: `frontend/composables/useCountUp.ts`

- [ ] **Step 1: Create the file**

```typescript
import { ref, watch, type Ref } from "vue";

/**
 * Animate a numeric ref from its current displayed value to a new target
 * over `durationMs`, using ease-out cubic. Pure requestAnimationFrame —
 * no library. The returned ref is updated each frame and can be rendered
 * directly with `{{ count }}`.
 */
export function useCountUp(target: Ref<number>, durationMs = 1200): Ref<number> {
  const current = ref(0);
  let raf = 0;

  function animate(from: number, to: number) {
    cancelAnimationFrame(raf);
    const startTs = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - startTs) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      current.value = Math.round(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  watch(
    target,
    (to) => animate(current.value, to ?? 0),
    { immediate: true },
  );
  return current;
}
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: no NEW errors from this file. (Pre-existing errors unchanged.)

- [ ] **Step 3: Do NOT commit.**

---

## Task 6: `loginModal` store + `LoginModal` component

**Files:**
- Create: `frontend/stores/loginModal.ts`
- Create: `frontend/components/LoginModal.vue`

- [ ] **Step 1: Create the store**

`frontend/stores/loginModal.ts`:

```typescript
import { defineStore } from "pinia";

/**
 * Global controller for the in-place login modal. Any component can
 * call `show(returnTo)` — the modal mounts in `app.vue` and is rendered
 * over the whole UI via Teleport.
 */
export const useLoginModalStore = defineStore("loginModal", {
  state: () => ({
    open: false,
    returnTo: null as string | null,
  }),
  actions: {
    show(returnTo: string | null = null) {
      this.open = true;
      this.returnTo = returnTo;
    },
    hide() {
      this.open = false;
      this.returnTo = null;
    },
  },
});
```

- [ ] **Step 2: Create the modal component**

`frontend/components/LoginModal.vue`:

```vue
<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm"
      @click.self="close"
      @keydown.esc.window="close"
    >
      <div class="login-modal-card relative w-full max-w-sm rounded-xl p-6">
        <div class="conic-border" aria-hidden="true" />
        <div class="relative">
          <button
            type="button"
            class="absolute right-0 top-0 text-gonka-muted hover:text-gonka-text"
            aria-label="Close"
            @click="close"
          >
            ✕
          </button>
          <div class="mb-6 flex flex-col items-center text-center">
            <span
              class="mb-3 grid h-12 w-12 place-items-center rounded-xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/40"
            >
              <svg viewBox="0 0 24 24" class="h-6 w-6" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M3 17l6-6 4 4 8-8" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M14 7h7v7" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <h2 class="text-base font-semibold text-white">Sign in</h2>
            <p class="mt-1 text-xs text-gonka-muted">
              Log in to access the full operator console
            </p>
          </div>
          <form class="space-y-4" @submit.prevent="submit">
            <div>
              <label class="label mb-1" for="login-modal-username">Username</label>
              <input
                id="login-modal-username"
                v-model="username"
                type="text"
                class="input"
                placeholder="sa or your email"
                autocomplete="username"
                :disabled="loading"
                autofocus
              />
            </div>
            <div>
              <label class="label mb-1" for="login-modal-password">Password</label>
              <input
                id="login-modal-password"
                v-model="password"
                type="password"
                class="input"
                placeholder="••••••••"
                autocomplete="current-password"
                :disabled="loading"
              />
            </div>
            <p v-if="error" class="text-xs text-red-300">{{ error }}</p>
            <button
              class="btn btn-primary w-full"
              type="submit"
              :disabled="loading || !username || !password"
            >
              {{ loading ? "Signing in…" : "Sign in" }}
            </button>
          </form>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const api = useApi();
const auth = useAuthStore();
const loginModal = useLoginModalStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

function errorMessage(e: unknown): string {
  const detail = (e as { data?: { detail?: string } })?.data?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

function close() {
  if (loading.value) return;
  loginModal.hide();
  username.value = "";
  password.value = "";
  error.value = "";
}

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await api.login(username.value.trim(), password.value);
    auth.setSession(res.token, res.username, res.role);
    const target = loginModal.returnTo;
    loginModal.hide();
    username.value = "";
    password.value = "";
    if (target) await navigateTo(target);
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-modal-card {
  background: linear-gradient(135deg, rgba(14, 19, 28, 0.95), rgba(18, 24, 38, 0.95));
}
.conic-border {
  position: absolute;
  inset: 0;
  background: conic-gradient(from 0deg, #22c55e, #06b6d4, #22c55e);
  border-radius: 0.75rem;
  animation: spin 8s linear infinite;
  z-index: 0;
  opacity: 0.45;
  padding: 1px;
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask-composite: exclude;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
```

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: no NEW errors from either file.

- [ ] **Step 4: Do NOT commit.**

---

## Task 7: `auth.global.ts` rewrite + `default.vue` gate + `AppTopbar.vue` polish

**Files:**
- Modify: `frontend/middleware/auth.global.ts` (full rewrite)
- Modify: `frontend/layouts/default.vue` (gate infoStore.refresh on auth)
- Modify: `frontend/components/AppTopbar.vue` (skip chips when no info)

- [ ] **Step 1: Rewrite `frontend/middleware/auth.global.ts`**

Replace the entire file contents with:

```typescript
/**
 * Global route guard.
 *
 * - /dashboard is public (no token needed)
 * - / for anonymous users → /dashboard (so the bare domain works for guests)
 * - any private path for anonymous users → /dashboard?login=1&return_to=...
 *   (dashboard page reads ?login=1 and opens the in-place login modal,
 *   then on success navigates to return_to)
 * - /login behaves as before (authenticated users bounced to /)
 * - admin/profile gating preserved for logged-in users
 */

const PUBLIC_PATHS = ["/dashboard"];
const REDIRECT_ROOT = ["/"];
const ADMIN_ONLY_PATHS = ["/tasks", "/schedule", "/settings", "/account"];

export default defineNuxtRouteMiddleware((to) => {
  if (import.meta.server) return;

  const auth = useAuthStore();
  auth.hydrate();

  if (to.path === "/login") {
    if (auth.isAuthenticated) return navigateTo("/");
    return;
  }

  if (PUBLIC_PATHS.includes(to.path)) return;

  if (!auth.isAuthenticated) {
    if (REDIRECT_ROOT.includes(to.path)) return navigateTo("/dashboard");
    return navigateTo({
      path: "/dashboard",
      query: { login: "1", return_to: to.fullPath },
    });
  }

  if (
    !auth.isAdmin &&
    ADMIN_ONLY_PATHS.some((p) => to.path === p || to.path.startsWith(`${p}/`))
  ) {
    return navigateTo("/");
  }
  if (to.path.startsWith("/profile") && auth.isAdmin) {
    return navigateTo("/");
  }
});
```

- [ ] **Step 2: Gate `infoStore.refresh()` in `frontend/layouts/default.vue`**

Replace the `<script setup>` block (currently lines 36-44):

```vue
<script setup lang="ts">
const infoStore = useInfoStore();
const auth = useAuthStore();

onMounted(() => {
  // /api/info requires a token; calling it as an anonymous visitor would
  // 401 and trigger the redirect-to-login fallback. Anonymous visitors on
  // /dashboard don't need this data anyway — the sidebar/topbar widgets
  // that consume infoStore hide their info-dependent chrome below.
  if (auth.isAuthenticated && !infoStore.loaded) {
    infoStore.refresh();
  }
});
</script>
```

- [ ] **Step 3: Polish `frontend/components/AppTopbar.vue`**

Find the `<div class="hidden items-center gap-2 md:flex">` block (currently lines 23-46) and wrap its CONTENTS so the three chips only render when `info` is truthy:

```vue
<div class="hidden items-center gap-2 md:flex">
  <template v-if="info">
    <div
      class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
    >
      <span class="font-mono text-gonka-muted">mode</span>
      <span class="font-semibold uppercase">{{ info.mode || "?" }}</span>
      <span
        class="ml-1 h-2 w-2 rounded-full"
        :class="info.configured ? 'bg-emerald-500' : 'bg-amber-500'"
      ></span>
    </div>
    <div
      class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
    >
      <span class="font-mono text-gonka-muted">model</span>
      <span class="font-mono">{{ shortModel }}</span>
    </div>
    <div
      class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
    >
      <span class="font-mono text-gonka-muted">workers</span>
      <span class="font-mono">{{ info.max_workers ?? "?" }}</span>
    </div>
  </template>
</div>
```

Also update the `pageTitle` computed to special-case `/dashboard`:

```typescript
const pageTitle = computed(() => {
  if (route.path === "/") return "Decisions";
  if (route.path === "/dashboard") return "Dashboard";
  if (route.path.startsWith("/tasks")) return "Tasks";
  if (route.path.startsWith("/settings")) return "Settings";
  return "Dashboard";
});

const subtitle = computed(() => {
  if (route.path === "/") return "Latest agent verdicts";
  if (route.path === "/dashboard") return "Public view";
  if (route.path.startsWith("/tasks")) return "Runs & schedules";
  if (route.path.startsWith("/settings")) return "Gonka connection";
  return "";
});
```

- [ ] **Step 4: Typecheck + build**

```bash
cd frontend && npx nuxi typecheck && npx nuxi build
```

Expected: no NEW errors; build succeeds.

- [ ] **Step 5: Do NOT commit.**

---

## Task 8: `AppSidebar.vue` — anonymous branch with locked tabs

**Files:**
- Modify: `frontend/components/AppSidebar.vue` (replace nav rendering and bottom user region)

- [ ] **Step 1: Update the template's `<nav>` block**

Replace the existing `<nav>` (currently lines 20-36) with:

```vue
<nav class="flex-1 space-y-1 px-3 py-4 text-sm">
  <template v-for="item in navItems" :key="item.to">
    <NuxtLink
      v-if="!item.locked"
      :to="item.to"
      class="group flex items-center gap-3 rounded-lg px-3 py-2 text-gonka-muted transition hover:bg-gonka-card hover:text-gonka-text"
      active-class="bg-gonka-card text-gonka-text shadow-card"
    >
      <span
        class="grid h-7 w-7 place-items-center rounded-md border border-gonka-border bg-gonka-card text-gonka-muted group-hover:text-emerald-400"
      >
        <component :is="item.icon" class="h-4 w-4" />
      </span>
      <span class="flex-1">{{ item.label }}</span>
      <span v-if="item.badge" class="chip text-[10px]">{{ item.badge }}</span>
    </NuxtLink>
    <button
      v-else
      type="button"
      class="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-gonka-muted/60 opacity-60 transition hover:opacity-90"
      @click="loginModal.show(item.to)"
    >
      <span
        class="grid h-7 w-7 place-items-center rounded-md border border-gonka-border bg-gonka-card text-gonka-muted/60"
      >
        <component :is="item.icon" class="h-4 w-4" />
      </span>
      <span class="flex-1">{{ item.label }}</span>
      <span class="text-[10px]">🔒</span>
    </button>
  </template>
</nav>
```

- [ ] **Step 2: Replace the "Connection" middle block AND the bottom user region**

Currently lines 38-88 are the Connection card + user pill. Replace the entire block (everything between the closing `</nav>` and the closing `</aside>`) with:

```vue
<!-- Connection panel (logged-in only) -->
<div
  v-if="auth.isAuthenticated"
  class="m-3 rounded-lg border border-gonka-border bg-gonka-card p-3 text-xs text-gonka-muted"
>
  <div class="mb-1 flex items-center justify-between">
    <span class="font-semibold text-gonka-text">Connection</span>
    <span
      class="inline-flex h-2 w-2 rounded-full"
      :class="info?.configured ? 'bg-emerald-500' : 'bg-amber-500'"
    ></span>
  </div>
  <div class="font-mono text-[11px] text-gonka-muted">
    {{ info?.mode?.toUpperCase() || "—" }}
    <span v-if="info?.configured" class="text-emerald-400">· ready</span>
    <span v-else class="text-amber-400">· not configured</span>
  </div>
  <div class="mt-1 truncate font-mono text-[11px]">
    {{ shortModel }}
  </div>
</div>

<!-- Bottom: user pill (logged in) OR Log-in CTA (anonymous) -->
<div class="border-t border-gonka-border p-3">
  <div v-if="auth.isAuthenticated" class="flex items-center gap-2 px-1">
    <span
      class="grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-semibold uppercase"
      :class="
        auth.isAdmin
          ? 'bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30'
          : 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30'
      "
    >
      {{ initials }}
    </span>
    <div class="min-w-0 flex-1 leading-tight">
      <div class="truncate text-xs font-medium text-gonka-text" :title="auth.username || ''">
        {{ auth.username || "—" }}
      </div>
      <div class="text-[10px] uppercase tracking-wider text-gonka-muted">
        {{ auth.isAdmin ? "Super admin" : "User" }}
      </div>
    </div>
    <button
      class="btn btn-ghost px-2 py-1"
      type="button"
      title="Sign out"
      @click="logout"
    >
      <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M16 17l5-5-5-5M21 12H9" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
  </div>
  <button
    v-else
    class="btn btn-primary w-full"
    type="button"
    @click="loginModal.show(null)"
  >
    Log in
  </button>
</div>
```

- [ ] **Step 3: Update `<script setup>` — add dashboard nav item, anonymous branch in navItems, import loginModal store**

Replace the existing `<script setup>` block. Add `loginModal`, add a `dashboardItem` definition, and modify `navItems` to a 3-way branch:

```vue
<script setup lang="ts">
import { h } from "vue";

const infoStore = useInfoStore();
const auth = useAuthStore();
const loginModal = useLoginModalStore();
const info = computed(() => infoStore.info);

const shortModel = computed(() => {
  const m = info.value?.deep_model;
  if (!m) return "no model";
  return m.split("/").pop() || m;
});

const initials = computed(() => {
  const name = auth.username || "";
  if (!name) return "?";
  return name.slice(0, 2).toUpperCase();
});

async function logout() {
  auth.clear();
  await navigateTo("/login");
}

const dashboardIcon = () =>
  h(
    "svg",
    { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
    [
      h("path", {
        d: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 9h8V3h-8z",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }),
    ],
  );

const accountIcon = () =>
  h(
    "svg",
    { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
    [
      h("circle", { cx: "9", cy: "8", r: "3.2" }),
      h("path", {
        d: "M3.5 19a5.5 5.5 0 0 1 11 0",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }),
      h("path", { d: "M17 8h4M19 6v4", "stroke-linecap": "round" }),
    ],
  );

const profileIcon = () =>
  h(
    "svg",
    { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
    [
      h("circle", { cx: "12", cy: "8", r: "3.4" }),
      h("path", {
        d: "M5 20a7 7 0 0 1 14 0",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }),
    ],
  );

const dashboardItem = {
  to: "/dashboard",
  label: "Dashboard",
  icon: dashboardIcon,
  locked: false,
};

const decisionsItem = {
  to: "/",
  label: "Decisions",
  icon: () =>
    h(
      "svg",
      { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
      [
        h("path", { d: "M3 3v18h18", "stroke-linecap": "round", "stroke-linejoin": "round" }),
        h("path", { d: "M7 14l3-3 3 3 5-6", "stroke-linecap": "round", "stroke-linejoin": "round" }),
      ],
    ),
  locked: false,
};

const adminOnlyItems = [
  {
    to: "/tasks",
    label: "Tasks",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "9" }),
          h("path", {
            d: "M10 8l5 4-5 4V8z",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            fill: "currentColor",
          }),
        ],
      ),
    locked: false,
  },
  {
    to: "/schedule",
    label: "Schedule",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "9" }),
          h("path", { d: "M12 7v5l3 2", "stroke-linecap": "round", "stroke-linejoin": "round" }),
        ],
      ),
    locked: false,
  },
  {
    to: "/settings",
    label: "Settings",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "3" }),
          h("path", {
            d: "M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.01a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.01a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.56 1.03z",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
          }),
        ],
      ),
    locked: false,
  },
];

const accountItem = { to: "/account", label: "Account Management", icon: accountIcon, locked: false };

const navItems = computed(() => {
  if (!auth.isAuthenticated) {
    // Anonymous: Dashboard is the only unlocked tab. Everything else is
    // visually present but click-triggers the login modal.
    return [
      dashboardItem,
      { ...decisionsItem, locked: true },
      ...adminOnlyItems.map((i) => ({ ...i, locked: true })),
      { ...accountItem, locked: true },
    ];
  }
  if (auth.isAdmin) {
    return [dashboardItem, decisionsItem, ...adminOnlyItems, accountItem];
  }
  return [
    dashboardItem,
    decisionsItem,
    { to: "/profile", label: "Personal Settings", icon: profileIcon, locked: false },
  ];
});
</script>
```

- [ ] **Step 4: Typecheck + build**

```bash
cd frontend && npx nuxi typecheck && npx nuxi build
```

Expected: no NEW errors; build succeeds.

- [ ] **Step 5: Do NOT commit.**

---

## Task 9: `DashboardBackground.vue`

**Files:**
- Create: `frontend/components/DashboardBackground.vue`

- [ ] **Step 1: Create the component**

```vue
<template>
  <div class="dashboard-bg pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
    <!-- Radial dark gradient base -->
    <div class="absolute inset-0 dashboard-bg-radial" />
    <!-- Hex grid pattern overlay -->
    <div class="absolute inset-0 dashboard-bg-hex" />
    <!-- Drifting particles -->
    <div
      v-for="(p, i) in particles"
      :key="i"
      class="dashboard-particle"
      :style="{
        left: p.left + '%',
        top: p.top + '%',
        animationDelay: p.delay + 's',
        animationDuration: p.duration + 's',
      }"
    />
    <!-- Bottom-right node-graph decoration -->
    <svg
      class="absolute bottom-0 right-0 h-72 w-72 text-emerald-500/10"
      viewBox="0 0 200 200"
      fill="none"
      stroke="currentColor"
      stroke-width="0.8"
    >
      <circle cx="40" cy="60" r="3" fill="currentColor" />
      <circle cx="120" cy="40" r="3" fill="currentColor" />
      <circle cx="160" cy="90" r="3" fill="currentColor" />
      <circle cx="100" cy="130" r="3" fill="currentColor" />
      <circle cx="50" cy="160" r="3" fill="currentColor" />
      <line x1="40" y1="60" x2="120" y2="40" />
      <line x1="120" y1="40" x2="160" y2="90" />
      <line x1="40" y1="60" x2="100" y2="130" />
      <line x1="100" y1="130" x2="160" y2="90" />
      <line x1="100" y1="130" x2="50" y2="160" />
    </svg>
  </div>
</template>

<script setup lang="ts">
// 8 deterministically-seeded particles. Positions/delays are fixed (not
// random per mount) so the layout is stable across hydration.
const particles = [
  { left: 12, top: 18, delay: 0, duration: 14 },
  { left: 28, top: 72, delay: 2, duration: 18 },
  { left: 45, top: 35, delay: 4, duration: 12 },
  { left: 62, top: 80, delay: 1, duration: 16 },
  { left: 78, top: 22, delay: 5, duration: 20 },
  { left: 88, top: 60, delay: 3, duration: 13 },
  { left: 8, top: 50, delay: 6, duration: 17 },
  { left: 55, top: 12, delay: 2.5, duration: 15 },
];
</script>

<style scoped>
.dashboard-bg-radial {
  background: radial-gradient(
    ellipse at 50% 30%,
    rgba(34, 197, 94, 0.06) 0%,
    rgba(6, 9, 15, 0) 60%
  );
}
.dashboard-bg-hex {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='46' viewBox='0 0 40 46'><path d='M20 0L40 11.5v23L20 46 0 34.5v-23z' fill='none' stroke='%2334d399' stroke-opacity='0.05' stroke-width='0.5'/></svg>");
  background-repeat: repeat;
  opacity: 0.6;
}
.dashboard-particle {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 8px #34d399;
  opacity: 0.4;
  animation: drift linear infinite;
}
@keyframes drift {
  0% { transform: translate(0, 0); opacity: 0; }
  20% { opacity: 0.5; }
  80% { opacity: 0.5; }
  100% { transform: translate(40px, -40px); opacity: 0; }
}
</style>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: clean for this file.

- [ ] **Step 3: Do NOT commit.**

---

## Task 10: `DashboardMetricCard.vue`

**Files:**
- Create: `frontend/components/DashboardMetricCard.vue`

- [ ] **Step 1: Create the component**

```vue
<template>
  <div class="metric-card relative overflow-hidden rounded-xl p-6">
    <div class="conic-border" aria-hidden="true" />
    <div class="relative">
      <div class="flex items-center gap-2 text-xs uppercase tracking-widest text-gonka-muted">
        <span class="inline-block h-3 w-1 rounded-sm bg-emerald-400" />
        {{ label }}
      </div>
      <div v-if="error" class="mt-6 font-mono text-6xl tracking-widest text-gonka-muted">
        —
      </div>
      <div
        v-else
        class="mt-6 font-mono text-6xl font-bold tracking-widest text-emerald-300"
        :style="{ textShadow: '0 0 24px rgba(34, 197, 94, 0.45)' }"
      >
        {{ animated.toLocaleString() }}
      </div>
      <div v-if="error" class="mt-2 text-xs text-amber-400">{{ error }}</div>
      <div v-else class="mt-2 h-3" />
      <div class="scanline mt-4" aria-hidden="true" />
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  label: string;
  value: number;
  error?: string | null;
}>();

const target = computed(() => props.value);
const animated = useCountUp(target);
</script>

<style scoped>
.metric-card {
  background: linear-gradient(135deg, rgba(14, 19, 28, 0.9), rgba(18, 24, 38, 0.9));
}
.conic-border {
  position: absolute;
  inset: 0;
  background: conic-gradient(from 0deg, #22c55e, #06b6d4, #22c55e);
  border-radius: 0.75rem;
  animation: spin 8s linear infinite;
  z-index: 0;
  opacity: 0.35;
  padding: 1px;
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask-composite: exclude;
}
.scanline {
  height: 2px;
  background: linear-gradient(90deg, transparent, #22c55e, transparent);
  animation: sweep 2.5s ease-in-out infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes sweep {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}
</style>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: clean for this file.

- [ ] **Step 3: Do NOT commit.**

---

## Task 11: `DashboardTickerCard.vue`

**Files:**
- Create: `frontend/components/DashboardTickerCard.vue`

- [ ] **Step 1: Create the component**

```vue
<template>
  <NuxtLink
    v-if="isClickable && auth.isAuthenticated"
    :to="linkTarget"
    :class="cardClass"
  >
    <div class="flex items-baseline justify-between gap-2">
      <span class="font-mono text-lg font-bold tracking-tight text-white">{{ card.ticker }}</span>
      <span class="font-mono text-[10px] text-gonka-muted">#{{ card.market_cap_rank }}</span>
    </div>
    <div class="mt-2">
      <RatingBadge :rating="card.rating ?? null" class="rating-glow" :data-rating="card.rating ?? ''" />
    </div>
    <div class="mt-3 space-y-0.5 text-[10px] text-gonka-muted">
      <div class="truncate font-mono">{{ shortModel }}</div>
      <div class="font-mono">{{ card.trade_date }}</div>
    </div>
  </NuxtLink>
  <div v-else :class="cardClass" @click="handleClick">
    <div class="flex items-baseline justify-between gap-2">
      <span class="font-mono text-lg font-bold tracking-tight text-white">{{ card.ticker }}</span>
      <span class="font-mono text-[10px] text-gonka-muted">#{{ card.market_cap_rank }}</span>
    </div>
    <div class="mt-2">
      <div v-if="card.no_decision" class="font-mono text-xs text-gonka-muted">─ ─ ─</div>
      <RatingBadge v-else :rating="card.rating ?? null" class="rating-glow" :data-rating="card.rating ?? ''" />
    </div>
    <div class="mt-3 space-y-0.5 text-[10px] text-gonka-muted">
      <template v-if="card.no_decision">
        <div>no analysis yet</div>
      </template>
      <template v-else>
        <div class="truncate font-mono">{{ shortModel }}</div>
        <div class="font-mono">{{ card.trade_date }}</div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { DashboardCard } from "~/composables/useApi";

const props = defineProps<{ card: DashboardCard }>();
const auth = useAuthStore();
const loginModal = useLoginModalStore();

const isClickable = computed(() => !props.card.no_decision);

const linkTarget = computed(
  () => `/decisions/${encodeURIComponent(props.card.ticker)}/${props.card.trade_date}`,
);

const cardClass = computed(() => [
  "ticker-card block rounded-lg p-4 transition",
  isClickable.value
    ? "cursor-pointer hover:-translate-y-1 hover:ring-2 hover:ring-emerald-500/40"
    : "cursor-default opacity-60",
]);

const shortModel = computed(() => {
  const m = props.card.deep_model;
  if (!m) return "";
  const tail = m.split("/").pop() || m;
  return tail.length > 14 ? tail.slice(0, 14) + "…" : tail;
});

function handleClick(e: Event) {
  if (!isClickable.value) return;
  // Anonymous + clickable: prevent any default and pop the login modal.
  e.preventDefault();
  loginModal.show(linkTarget.value);
}
</script>

<style scoped>
.ticker-card {
  background: rgba(18, 24, 38, 0.85);
  border: 1px solid #1e2434;
}
.rating-glow[data-rating="Buy"] { box-shadow: 0 0 18px rgba(34, 197, 94, 0.5); }
.rating-glow[data-rating="Sell"] { box-shadow: 0 0 18px rgba(239, 68, 68, 0.5); }
.rating-glow[data-rating="Hold"] { box-shadow: 0 0 12px rgba(148, 163, 184, 0.4); }
.rating-glow[data-rating="Overweight"] { box-shadow: 0 0 18px rgba(132, 204, 22, 0.5); }
.rating-glow[data-rating="Underweight"] { box-shadow: 0 0 18px rgba(245, 158, 11, 0.5); }
</style>
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && npx nuxi typecheck
```

Expected: clean for this file.

- [ ] **Step 3: Do NOT commit.**

---

## Task 12: `dashboard.vue` page + `app.vue` modal mount

**Files:**
- Create: `frontend/pages/dashboard.vue`
- Modify: `frontend/app.vue`

- [ ] **Step 1: Create the page**

`frontend/pages/dashboard.vue`:

```vue
<template>
  <div class="relative">
    <DashboardBackground />

    <div class="space-y-8 relative z-10">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <h1 class="font-mono text-2xl font-bold tracking-widest dashboard-title-gradient">
          TRADINGAGENTS · ON-CHAIN ALPHA
        </h1>
        <div class="flex items-center gap-3">
          <span class="dashboard-status-pulse" aria-hidden="true" />
          <span class="text-xs uppercase tracking-wider text-gonka-muted">live</span>
          <button class="btn btn-ghost" type="button" :disabled="loading" @click="reload">
            ↻ Refresh
          </button>
        </div>
      </header>

      <section class="grid gap-4 sm:grid-cols-2">
        <DashboardMetricCard
          label="Market Days"
          :value="stats?.dates_analyzed ?? 0"
          :error="statsError"
        />
        <DashboardMetricCard
          label="Tickers Covered"
          :value="stats?.tickers_analyzed ?? 0"
          :error="statsError"
        />
      </section>

      <section>
        <div class="mb-3 flex items-center gap-2">
          <span class="h-4 w-1 rounded-sm bg-emerald-400" />
          <h2 class="text-sm font-semibold uppercase tracking-wider text-gonka-text">
            SP TOP 20 · Latest Verdicts
          </h2>
        </div>
        <div
          v-if="topError"
          class="card border border-red-500/40 bg-red-500/5 p-5"
        >
          <p class="text-sm text-red-200">{{ topError }}</p>
          <button class="btn btn-ghost mt-3" type="button" @click="reload">
            Retry
          </button>
        </div>
        <div
          v-else
          class="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5"
        >
          <DashboardTickerCard
            v-for="card in top"
            :key="card.ticker"
            :card="card"
          />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { DashboardStats, DashboardCard } from "~/composables/useApi";

const api = useApi();
const auth = useAuthStore();
const route = useRoute();
const loginModal = useLoginModalStore();

const stats = ref<DashboardStats | null>(null);
const top = ref<DashboardCard[]>([]);
const loading = ref(false);
const statsError = ref<string | null>(null);
const topError = ref<string | null>(null);

async function reload() {
  loading.value = true;
  statsError.value = null;
  topError.value = null;
  const results = await Promise.allSettled([
    api.getDashboardStats(),
    api.getDashboardTop(),
  ]);
  if (results[0].status === "fulfilled") {
    stats.value = results[0].value;
  } else {
    statsError.value = "data unavailable";
  }
  if (results[1].status === "fulfilled") {
    top.value = results[1].value;
  } else {
    const e = results[1].reason as {
      data?: { detail?: string };
      message?: string;
    };
    topError.value =
      e?.data?.detail || e?.message || "Could not load latest verdicts.";
  }
  loading.value = false;
}

onMounted(async () => {
  await reload();
  if (!auth.isAuthenticated && route.query.login === "1") {
    const returnTo =
      typeof route.query.return_to === "string" ? route.query.return_to : null;
    loginModal.show(returnTo);
  }
});
</script>

<style scoped>
.dashboard-title-gradient {
  background: linear-gradient(90deg, #34d399, #06b6d4, #34d399);
  background-size: 200% auto;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: title-shimmer 6s linear infinite;
}
.dashboard-status-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes title-shimmer {
  to { background-position: 200% center; }
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6); }
  50% { box-shadow: 0 0 0 8px rgba(34, 197, 94, 0); }
}
</style>
```

- [ ] **Step 2: Mount the login modal globally in `frontend/app.vue`**

Replace the entire file contents:

```vue
<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
  <LoginModal v-if="loginModal.open" />
</template>

<script setup lang="ts">
const loginModal = useLoginModalStore();
</script>
```

- [ ] **Step 3: Typecheck + build**

```bash
cd frontend && npx nuxi typecheck && npx nuxi build
```

Expected: no NEW errors; build succeeds.

- [ ] **Step 4: Do NOT commit.**

---

## Task 13: End-to-end verification (manual)

This task has no code changes. Confirms the whole feature works before declaring done. All backend tests must already be green from Tasks 1–3.

- [ ] **Step 1: Run the full backend suite**

```bash
pytest tests/test_dashboard_db.py tests/test_dashboard_api.py -v
```

Expected: 13 PASS (8 db + 5 api).

- [ ] **Step 2: Start the app**

```bash
./start.sh
```

Wait until both backend (`:8000`) and frontend (`:3000`) are listening.

- [ ] **Step 3: Walk the checklist in a browser**

Tick each item off. If any fails, stop and fix before continuing.

**Anonymous flows:**
- [ ] `GET /` (private/incognito tab) → lands on `/dashboard`
- [ ] Page is fully styled: holographic title, pulsing status dot, hex-grid backdrop, drifting particles, rotating border on metric cards, count-up numbers, scanline animation, glowing rating badges
- [ ] Top 20 grid: 5 cols on `lg`, 3 on `md`, 2 on `sm`
- [ ] Cards with no decision (if any) are dimmed and non-interactive
- [ ] Click any **locked sidebar tab** (Decisions / Tasks / etc.) → modal opens
- [ ] Click any **interactive ticker card** → modal opens
- [ ] ESC closes modal; click on overlay outside card closes modal
- [ ] Submit bad credentials → red error in modal, modal stays open
- [ ] Submit valid credentials → modal closes; if click originated from a card, you land on the decision detail; if from a sidebar tab, you land on that tab
- [ ] Visit `/tasks` directly while anonymous → land on `/dashboard?login=1&return_to=%2Ftasks`, modal opens with return target preserved
- [ ] Hit Refresh button → metric numbers smoothly transition from current to new
- [ ] Stop backend → metric cards show `—` + amber "data unavailable"; Top 20 shows red error + Retry button

**Logged-in flows:**
- [ ] Logged-in user `GET /` → still sees the original Decisions list (no regression)
- [ ] Logged-in user `GET /dashboard` → renders normally; sidebar shows Dashboard tab active; no `?login=1` is read into a modal even if URL has it
- [ ] Logged-in admin → sees all sidebar items as before
- [ ] Logged-in non-admin user → sees Dashboard, Decisions, Personal Settings only
- [ ] Sign out from sidebar → returns to `/login`

- [ ] **Step 4: Do NOT commit.** Report back to the orchestrator.
