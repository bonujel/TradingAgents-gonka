# Decision Page Redesign — List + On-Demand Detail

**Date:** 2026-05-26
**Status:** Approved (pending user spec review)
**Scope:** Frontend dashboard `/` (decision list) and backend `/api/decisions*` endpoints

## Problem

The current decision dashboard (`frontend/pages/index.vue`) loads every decision row for the selected trade date in a single request, including the full markdown payload for six per-ticker reports (market, sentiment, news, fundamentals, investment plan, trader plan) plus the PM verdict. All filtering and pagination is then performed client-side by slicing the in-memory array.

This is wasteful at three levels:

1. **Network** — a single date with the S&P 500 universe can return hundreds of rows × multiple long markdown blobs, while the user is typically scanning headers and only reading one or two tickers in depth.
2. **Render** — each card mounts every collapsed section and its `Markdown` renderer even when collapsed, slowing first paint.
3. **Server** — `SELECT *` on the `decisions` table moves a lot of bytes through SQLite and HTTP for data that will not be read.

The desired experience: the list page shows a compact, scannable row per ticker (ticker · rating · date · model · stored timestamp), and a click drills into a dedicated detail page that fetches the full report payload on demand.

## Goals

- Load the list page with a small, fixed payload regardless of how many decisions exist for a date.
- Move pagination and filtering to the server, so totals and page counts are accurate without holding every row in memory.
- Fetch full markdown reports only when the user explicitly opens a ticker's detail page.
- Preserve filter / page state across navigation (list → detail → back) and on refresh, via URL query params.
- Keep the change focused on the decision dashboard; no broader refactor.

## Non-Goals

- Client-side caching of detail payloads (each detail visit re-fetches).
- Cross-date search.
- Inline expansion of summary cards (we chose a dedicated detail page instead).
- SSR or prefetch — the frontend stays in its current SPA-ish Nuxt mode.
- Frontend test infrastructure (the repo has none today; out of scope to introduce).

## Architecture

Two pages, three endpoints, on-demand detail fetch:

```
List page  /                                  →  GET /api/decisions       (paginated summary)
                                              →  GET /api/decisions/models?date=  (model facet)
                                              →  GET /api/decisions/dates  (unchanged)

Detail page  /decisions/[ticker]/[trade_date] →  GET /api/decisions/{ticker}/{trade_date}  (unchanged)
```

The list page persists view state (`date`, `ticker`, `rating`, `model`, `page`) in URL query params. Browser back from the detail page restores the prior list state without any custom restore logic. A shareable URL like `/?date=2026-05-26&page=2&rating=Buy` reproduces the same view on a different client.

## Backend Changes

### `app/db.py`

Add two new query helpers; leave `list_decisions`, `upsert_decision`, and the schema alone.

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
    """Return (rows, total_count). rows excludes markdown blob columns."""
```

- Selects `id, ticker, trade_date, rating, deep_model, created_at, (error IS NOT NULL) AS has_error`.
- Same `WHERE` is applied to a `SELECT COUNT(*)` inside the same connection.
- Ordering: `trade_date DESC, ticker ASC` (matches existing `list_decisions`).
- Slicing: `LIMIT page_size OFFSET (page - 1) * page_size`.

```python
def list_distinct_models(
    *, trade_date: str, path: Optional[Path] = None
) -> list[str]:
    """Distinct deep_model values for a given trade_date, alphabetically sorted, NULLs excluded."""
```

### `app/api.py`

Replace the existing `/api/decisions` handler (breaking change: response shape changes from list to envelope). Add the new models facet endpoint. Leave `/api/decisions/dates` and `/api/decisions/{ticker}/{trade_date}` unchanged.

```python
@app.get("/api/decisions")
def list_decisions(
    date: str = Query(...),                              # required
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


@app.get("/api/decisions/models")
def list_decision_models(date: str = Query(...)) -> list[str]:
    return db.list_distinct_models(trade_date=date)
```

**Design decisions worth flagging:**

- `date` is required. Returning a paginated slice of every decision ever recorded has no UI use, and making it optional invites accidental full-table scans.
- `ticker`, `rating`, `model` use exact equality (uppercase normalisation for ticker, matching `list_decisions`). Substring search is not introduced.
- Response envelope `{rows, total, page, page_size}` is the same shape we would want for any future paginated list endpoint, so it sets a sensible house style.

### Backwards compatibility

`/api/decisions` is only called from the Nuxt frontend in this repository (verified by grepping `useApi` and the CLI). The shape change is contained.

## Frontend Changes

### `frontend/composables/useApi.ts`

- Add `DecisionSummaryRow` interface (id, ticker, trade_date, rating, deep_model, created_at, has_error).
- Add `DecisionListResponse` interface (`{ rows: DecisionSummaryRow[]; total: number; page: number; page_size: number }`).
- Change `listDecisions(params)` return type to `Promise<DecisionListResponse>`, and extend params to include `rating`, `model`, `page`, `page_size`.
- Add `listDecisionModels(date)` → `Promise<string[]>`.
- Keep `getDecision` and `DecisionRow` unchanged.

### `frontend/pages/index.vue` (list page)

- Drop client-side filter and slice logic.
- Drive state from URL query params via `useRoute` + `router.replace`. Watch `selectedDate`, `tickerFilter`, `selectedRating`, `selectedModel`, `page` and reflect into the URL.
- `loadRows()` calls `api.listDecisions({ date, ticker, rating, model, page, page_size })` and stores the envelope.
- Model dropdown is populated by `api.listDecisionModels(date)`, called whenever `selectedDate` changes.
- Rating dropdown uses the existing static `RATING_ORDER`.
- Pagination UI is unchanged but `totalPages = Math.ceil(total / page_size)` comes from the server.
- Empty / loading / error states reuse existing components.
- Rendering switches from `<DecisionCard>` to `<DecisionSummaryCard>` (new component).

### `frontend/pages/decisions/[ticker]/[trade_date].vue` (new detail page)

- `onMounted` → `api.getDecision(ticker, tradeDate)`.
- Renders the full report via the existing `DecisionCard.vue` (which already handles all sections).
- Header includes a `← Back` link that calls `router.back()` so the list page recovers its query-driven state.
- Three branches: loading skeleton, 404 ("Decision not found" + back link), generic fetch error (message + Retry button).

### `frontend/components/DecisionSummaryCard.vue` (new)

A compact, single-row card that mirrors the header of the current `DecisionCard.vue` and nothing else:

```
+-------------------------------------------------------------------+
| AAPL  [HOLD]  2026-05-26  Qwen3-235B-A22B-Instruct-25...          |
|                                         stored 2026-05-26 02:51   |
+-------------------------------------------------------------------+
```

- Whole card is a `<NuxtLink>` to `/decisions/{ticker}/{trade_date}`.
- Hover state adds a subtle border/shadow to signal interactivity.
- When `has_error` is true, render a red "errored" chip alongside the rating slot.
- No markdown rendering, no collapse sections.

### `frontend/components/DecisionCard.vue`

Keep as is. Detail page reuses it directly.

## Data Flow

1. User opens `/`. No query params → fetch dates, default `selectedDate` to most recent, write `?date=...` to URL.
2. User changes a filter or page → URL query updates → watcher fires → `loadRows()` hits `/api/decisions`.
3. User clicks a summary card → `NuxtLink` navigates to `/decisions/AAPL/2026-05-26`.
4. Detail page mounts → fetches full row → renders `DecisionCard`.
5. User clicks `← Back` → `router.back()` → list page restores from URL query, no extra logic needed.
6. User refreshes a list URL with filters → state restored from URL on mount.

## Error Handling

| Scenario | Behaviour |
|---|---|
| `/api/decisions` fails | Inline error block above the list with a Retry button; existing rows stay visible. |
| `/api/decisions/models` fails | Model dropdown falls back to a single "All models" option; surface a small inline warning. |
| Empty list | Existing `EmptyState` ("No matching decisions"). |
| Detail 404 | Detail page renders "Decision not found" + back link. |
| Detail fetch fails | Detail page renders error message + Retry button. |
| `row.has_error` true on summary card | Red "errored" chip on the card; the detail page renders the existing red error block (no change). |
| 401 anywhere | Existing `useApi` token-expiry path redirects to `/login`. |

## Testing

### Backend (pytest)

- `test_db_list_decisions_summary_pagination` — total count, page slicing, ordering, page beyond range returns empty rows with correct total.
- `test_db_list_decisions_summary_filters` — ticker, rating, deep_model filters individually and combined.
- `test_db_list_decisions_summary_excludes_blobs` — returned rows do not contain `final_decision`, `market_report`, etc.
- `test_db_list_distinct_models` — distinct, NULL excluded, alphabetical, scoped to trade_date.
- `test_api_decisions_envelope_shape` — response is `{rows, total, page, page_size}`.
- `test_api_decisions_requires_date` — missing `date` returns 422.
- `test_api_decisions_page_size_bounds` — page_size > 200 rejected; page < 1 rejected.
- `test_api_decisions_models_endpoint` — happy path and empty-date case.
- `test_api_decision_detail_unchanged` — existing happy path + 404 (add if not already present).

### Frontend

The repo has no frontend test infrastructure today (no `vitest` / `playwright` config under `frontend/`). Introducing it is out of scope for this change. Manual verification checklist:

- Pick a date with many decisions → list paginates correctly.
- Apply ticker / rating / model filters → totals and page count update.
- Click a card → detail loads, all reports render.
- Browser back → list state (date, filters, page) restored.
- Refresh the list URL with query params → same view.
- Direct visit to a non-existent detail URL → 404 view.
- Direct visit to a detail URL for an errored run → error block renders.

## Files Touched

```
app/db.py                                             (add 2 functions)
app/api.py                                            (rewrite 1 handler, add 1)
frontend/composables/useApi.ts                        (types + 1 new call)
frontend/pages/index.vue                              (rewire to server-side)
frontend/pages/decisions/[ticker]/[trade_date].vue    (new)
frontend/components/DecisionSummaryCard.vue           (new)
tests/test_decisions_api.py                           (new file: API endpoint tests)
tests/test_decisions_db.py                            (new file: db helper tests)
```

## Open Questions

None. All decisions captured above.
