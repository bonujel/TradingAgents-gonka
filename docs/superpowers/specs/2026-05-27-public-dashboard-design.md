# Public Dashboard — Anonymous Landing + Sci-Fi UI

**Date:** 2026-05-27
**Status:** Approved (pending user spec review)
**Scope:** New public `/dashboard` page, sidebar/auth changes to allow anonymous access, global login modal, two new public backend endpoints.

## Problem

The operator console currently requires login for every page — anonymous visitors get bounced to `/login` immediately. There is no public showcase for the project: someone arriving from the marketing site or social link sees only a credential prompt.

The desired experience: anonymous visitors land on a sci-fi-styled dashboard that proves the platform is alive and producing analyses, with prominent CTAs to log in. Other navigation remains visible but gated; clicking a gated tab pops a login modal in-place rather than yanking the user to a separate page.

## Goals

- Expose a single public dashboard at `/dashboard` that requires no login.
- Redirect anonymous root (`/`) visits to `/dashboard` (so the marketing-friendly URL is just the bare domain).
- Show two pieces of headline content publicly:
  1. Operational stats — distinct trade dates analyzed, distinct tickers analyzed.
  2. Latest verdict per S&P 500 top-20 ticker (by market cap), as a grid of colored cards.
- Keep all other pages logged-in only; clicking them while anonymous opens a login modal that, on success, navigates to the originally-requested route.
- Visual style: pushed-harder cyber / on-chain vibe (rotating conic gradient borders, holographic title text, count-up animated digits, hex-grid backdrop, drifting particles, glow halos on rating badges). Pure CSS + one tiny composable — no animation library.

## Non-Goals

- Auto-refresh of dashboard data (manual Refresh button only — decision data is daily-cadence).
- Light mode (project is dark-only).
- Customizable widgets / dashboard editor.
- Backend caching of `/api/dashboard/*` (payload is tiny; SQLite query is already fast).
- 3D mouse-tilt on cards, mouse-tracking parallax, sound effects, or any opt-in visual flourish beyond what's listed in this spec.
- Internationalization.
- Data export / "dashboard screenshot" history.
- Mobile-first redesign (mobile already works because the existing sidebar hides on `<lg`; dashboard content is responsive but not re-thought).

## Architecture

```
Routes
  /                     → if anonymous, redirect to /dashboard
                        → if logged in, original decisions list (unchanged)
  /dashboard            → public; renders for everyone
  /decisions/...        → logged-in only (unchanged)
  /tasks /schedule
   /settings /account   → admin-only (unchanged)
  /profile              → user-only (unchanged)
  /login                → unchanged; still used as primary entry, but no
                          longer the only entry (modal also works)

Anonymous → private path:
  any private path while anonymous
    → redirect to /dashboard?login=1&return_to=<original>
    → dashboard onMount reads query, opens login modal, fills returnTo
    → on success, modal navigates to returnTo

API
  GET /api/dashboard/stats   (PUBLIC, new)
    → { dates_analyzed: int, tickers_analyzed: int }
  GET /api/dashboard/top     (PUBLIC, new)
    → [ { ticker, market_cap_rank, rating, deep_model,
          trade_date, created_at, has_error }
        | { ticker, market_cap_rank, no_decision: true } ] × 20
  GET /api/health, /api/auth/login   (PUBLIC, unchanged)
  Everything else                    (TOKEN-GATED, unchanged)
```

The login modal is owned by a small Pinia store (`useLoginModalStore`) so any component — sidebar, ticker card, route guard — can trigger it without prop drilling.

## Backend Changes

### `app/db.py`

Three new helpers, no schema changes:

```python
def count_distinct_dates(*, path: Optional[Path] = None) -> int:
    """Total number of distinct trade_dates with at least one decision."""
    with connect(path) as conn:
        return int(conn.execute(
            "SELECT COUNT(DISTINCT trade_date) AS n FROM decisions"
        ).fetchone()["n"])


def count_distinct_tickers(*, path: Optional[Path] = None) -> int:
    """Total number of distinct tickers ever analyzed."""
    with connect(path) as conn:
        return int(conn.execute(
            "SELECT COUNT(DISTINCT ticker) AS n FROM decisions"
        ).fetchone()["n"])


def get_latest_decision_per_ticker(
    *, tickers: list[str], path: Optional[Path] = None
) -> dict[str, sqlite3.Row]:
    """For each ticker, return its most recent decision (summary columns only).

    Tickers without any stored decision are simply absent from the returned
    dict. The caller fills the gap with a placeholder card.

    One SQL round-trip; avoids N+1 by joining each ticker to its MAX(trade_date)
    in a subquery.
    """
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    upper = [t.upper() for t in tickers]
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

### `app/api.py`

Two new route handlers + one private helper:

```python
@app.get("/api/dashboard/stats")
def dashboard_stats() -> dict[str, int]:
    return {
        "dates_analyzed": db.count_distinct_dates(),
        "tickers_analyzed": db.count_distinct_tickers(),
    }


@app.get("/api/dashboard/top")
def dashboard_top() -> list[dict[str, Any]]:
    try:
        tickers = get_top_tickers(n=20)
    except Exception as exc:
        # get_top_tickers hits Wikipedia. If it cannot fetch (network down,
        # cache empty, rate-limited), we surface a 503 — the dashboard knows
        # how to degrade. Stats endpoint is unaffected.
        raise HTTPException(status_code=503, detail=f"top tickers unavailable: {exc}") from exc
    latest = db.get_latest_decision_per_ticker(tickers=tickers)
    return [_to_dashboard_card(t, rank, latest.get(t)) for rank, t in enumerate(tickers, start=1)]


def _to_dashboard_card(
    ticker: str, rank: int, row: Optional[sqlite3.Row]
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

### `app/auth.py`

Extend `_PUBLIC_PATHS` to skip token validation for the new endpoints:

```python
_PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/dashboard/stats",   # new
    "/api/dashboard/top",     # new
})
```

No other auth changes.

## Frontend Changes

### `frontend/middleware/auth.global.ts`

Rewrite the route guard with three branches:

1. `/login` itself — unchanged (logged-in users bounced to `/`).
2. Public paths (`/dashboard`) — always allowed.
3. Anonymous accessing anything else:
   - `/` → redirect to `/dashboard` (no `?login=1`; visitor may just be browsing).
   - any other private path → redirect to `/dashboard?login=1&return_to=<original full path>`.
4. Logged-in path-role enforcement — original admin/profile logic preserved.

```typescript
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

  if (!auth.isAdmin && ADMIN_ONLY_PATHS.some((p) => to.path === p || to.path.startsWith(`${p}/`))) {
    return navigateTo("/");
  }
  if (to.path.startsWith("/profile") && auth.isAdmin) return navigateTo("/");
});
```

### `frontend/stores/loginModal.ts` (new)

```typescript
export const useLoginModalStore = defineStore("loginModal", {
  state: () => ({ open: false, returnTo: null as string | null }),
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

### `frontend/composables/useApi.ts`

Add two types and two methods:

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

// inside the returned object:
getDashboardStats: () => request<DashboardStats>("/api/dashboard/stats"),
getDashboardTop: () => request<DashboardCard[]>("/api/dashboard/top"),
```

The existing `request` helper attaches `Authorization` only when a token exists, so anonymous calls work without changes.

### `frontend/composables/useCountUp.ts` (new)

```typescript
import { ref, watch, type Ref } from "vue";

export function useCountUp(target: Ref<number>, durationMs = 1200): Ref<number> {
  const current = ref(0);
  let raf = 0;
  function animate(from: number, to: number) {
    cancelAnimationFrame(raf);
    const startTs = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - startTs) / durationMs);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3);
      current.value = Math.round(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }
  watch(target, (to, from) => animate(typeof from === "number" ? current.value : 0, to), { immediate: true });
  return current;
}
```

Pure RAF, no library, ~25 lines.

### `frontend/components/AppSidebar.vue`

Three rendering branches:

| Branch | Items |
|---|---|
| Anonymous | Dashboard (link), Decisions (locked button), Tasks/Schedule/Settings (locked buttons), Account (locked button). Bottom: "Log in" CTA. |
| Logged-in user | Dashboard, Decisions, Personal Settings. Bottom: user pill. |
| Logged-in admin | Dashboard, Decisions, Tasks, Schedule, Settings, Account Management. Bottom: user pill. |

A `locked: boolean` flag on each nav item drives the rendering switch in the template:

```vue
<template v-for="item in navItems" :key="item.to">
  <NuxtLink v-if="!item.locked" :to="item.to" ...>
    <Icon /> {{ item.label }}
  </NuxtLink>
  <button
    v-else
    type="button"
    class="nav-locked-button"
    @click="loginModal.show(item.to)"
  >
    <Icon /> {{ item.label }}
    <span class="ml-auto text-[10px] uppercase tracking-wider text-gonka-muted/60">
      🔒
    </span>
  </button>
</template>
```

The bottom user region renders a "Log in" button when `!auth.isAuthenticated`, which also calls `loginModal.show()` (no returnTo — just authenticate; dashboard stays put afterward).

### `frontend/pages/dashboard.vue` (new)

```vue
<template>
  <DashboardBackground />
  <div class="space-y-8 relative z-10">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <h1 class="font-mono text-2xl font-bold tracking-widest dashboard-title-gradient">
        TRADINGAGENTS · ON-CHAIN ALPHA
      </h1>
      <div class="flex items-center gap-3">
        <span class="dashboard-status-pulse" />
        <span class="text-xs uppercase tracking-wider text-gonka-muted">live</span>
        <button class="btn btn-ghost" :disabled="loading" @click="reload">
          ↻ Refresh
        </button>
      </div>
    </header>

    <section class="grid gap-4 sm:grid-cols-2">
      <DashboardMetricCard label="Market Days" :value="stats?.dates_analyzed ?? 0" :error="statsError" />
      <DashboardMetricCard label="Tickers Covered" :value="stats?.tickers_analyzed ?? 0" :error="statsError" />
    </section>

    <section>
      <div class="mb-3 flex items-center gap-2">
        <span class="h-4 w-1 rounded-sm bg-emerald-400" />
        <h2 class="text-sm font-semibold uppercase tracking-wider text-gonka-text">
          SP TOP 20 · Latest Verdicts
        </h2>
      </div>
      <div v-if="topError" class="card border border-red-500/40 bg-red-500/5 p-5">
        <p class="text-sm text-red-200">{{ topError }}</p>
        <button class="btn btn-ghost mt-3" type="button" @click="reload">Retry</button>
      </div>
      <div v-else class="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
        <DashboardTickerCard v-for="card in top" :key="card.ticker" :card="card" />
      </div>
    </section>
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
  const results = await Promise.allSettled([api.getDashboardStats(), api.getDashboardTop()]);
  if (results[0].status === "fulfilled") stats.value = results[0].value;
  else statsError.value = "data unavailable";
  if (results[1].status === "fulfilled") top.value = results[1].value;
  else {
    const e = results[1].reason as { data?: { detail?: string }; message?: string };
    topError.value = e?.data?.detail || e?.message || "Could not load latest verdicts.";
  }
  loading.value = false;
}

onMounted(async () => {
  await reload();
  // Deep link with ?login=1 → open modal with return path
  if (!auth.isAuthenticated && route.query.login === "1") {
    const returnTo = typeof route.query.return_to === "string" ? route.query.return_to : null;
    loginModal.show(returnTo);
  }
});
</script>
```

### `frontend/components/DashboardBackground.vue` (new)

Full-screen fixed decoration. Layered:

- Radial gradient base (`bg-radial-gradient` via inline style or arbitrary Tailwind).
- Hex-grid SVG pattern at ~5% opacity, tiled.
- 8 absolutely-positioned `<div>` particles with CSS `@keyframes` drift (random `top`/`left`/`animation-delay` set via inline style at mount).
- Bottom-right faded "blockchain node graph" decorative SVG, `pointer-events: none`.

Pure CSS — no JS animation loop.

### `frontend/components/DashboardMetricCard.vue` (new)

```vue
<template>
  <div class="metric-card relative overflow-hidden rounded-xl p-6">
    <div class="conic-border" aria-hidden="true" />
    <div class="relative">
      <div class="text-xs uppercase tracking-widest text-gonka-muted">▌ {{ label }}</div>
      <div v-if="error" class="mt-6 font-mono text-6xl text-gonka-muted">—</div>
      <div v-else class="mt-6 font-mono text-6xl font-bold tracking-widest text-emerald-300 drop-shadow-[0_0_24px_rgba(34,197,94,0.4)]">
        {{ animated.toLocaleString() }}
      </div>
      <div v-if="error" class="mt-2 text-xs text-amber-400">{{ error }}</div>
      <div class="scanline mt-4" aria-hidden="true" />
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ label: string; value: number; error?: string | null }>();
const target = computed(() => props.value);
const animated = useCountUp(target);
</script>

<style scoped>
.metric-card { background: linear-gradient(135deg, rgba(14,19,28,0.9), rgba(18,24,38,0.9)); }
.conic-border {
  position: absolute; inset: -1px;
  background: conic-gradient(from 0deg, #22c55e, #06b6d4, #22c55e);
  border-radius: 0.75rem;
  animation: spin 8s linear infinite;
  z-index: 0; opacity: 0.4;
  mask: linear-gradient(black, black) content-box, linear-gradient(black, black);
  mask-composite: exclude; padding: 1px;
}
.scanline { height: 2px; background: linear-gradient(90deg, transparent, #22c55e, transparent); animation: sweep 2.5s ease-in-out infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes sweep { 0%,100% { transform: translateX(-100%); } 50% { transform: translateX(100%); } }
</style>
```

### `frontend/components/DashboardTickerCard.vue` (new)

```vue
<template>
  <component
    :is="rootTag"
    :to="linkTarget"
    :class="cardClass"
    @click="handleClick"
  >
    <div class="flex items-baseline justify-between gap-2">
      <span class="font-mono text-lg font-bold tracking-tight text-white">{{ card.ticker }}</span>
      <span class="text-[10px] font-mono text-gonka-muted">#{{ card.market_cap_rank }}</span>
    </div>
    <div class="mt-2">
      <div v-if="card.no_decision" class="text-xs text-gonka-muted">─ ─ ─</div>
      <RatingBadge v-else :rating="card.rating" class="rating-glow" :data-rating="card.rating" />
    </div>
    <div class="mt-3 text-[10px] text-gonka-muted">
      <div v-if="card.no_decision">no analysis yet</div>
      <template v-else>
        <div class="truncate font-mono">{{ shortModel }}</div>
        <div class="font-mono">{{ card.trade_date }}</div>
      </template>
    </div>
  </component>
</template>

<script setup lang="ts">
const props = defineProps<{ card: DashboardCard }>();
const auth = useAuthStore();
const loginModal = useLoginModalStore();

const isClickable = computed(() => !props.card.no_decision);
const rootTag = computed(() => (isClickable.value && auth.isAuthenticated ? "NuxtLink" : "div"));
const linkTarget = computed(() =>
  isClickable.value && auth.isAuthenticated
    ? `/decisions/${encodeURIComponent(props.card.ticker)}/${props.card.trade_date}`
    : undefined
);
const cardClass = computed(() => [
  "ticker-card block rounded-lg p-4 transition",
  isClickable.value ? "hover:-translate-y-1 hover:ring-2 hover:ring-emerald-500/40" : "opacity-60",
]);
const shortModel = computed(() => {
  const m = props.card.deep_model;
  if (!m) return "";
  const tail = m.split("/").pop() || m;
  return tail.length > 14 ? tail.slice(0, 14) + "…" : tail;
});
function handleClick(e: Event) {
  if (!isClickable.value) return;
  if (auth.isAuthenticated) return; // NuxtLink handles navigation
  e.preventDefault();
  loginModal.show(linkTarget.value ?? null);
}
</script>

<style scoped>
.ticker-card { background: rgba(18,24,38,0.85); border: 1px solid #1E2434; }
.rating-glow[data-rating="Buy"] { box-shadow: 0 0 18px rgba(34,197,94,0.5); }
.rating-glow[data-rating="Sell"] { box-shadow: 0 0 18px rgba(239,68,68,0.5); }
.rating-glow[data-rating="Hold"] { box-shadow: 0 0 12px rgba(148,163,184,0.4); }
.rating-glow[data-rating="Overweight"] { box-shadow: 0 0 18px rgba(132,204,22,0.5); }
.rating-glow[data-rating="Underweight"] { box-shadow: 0 0 18px rgba(245,158,11,0.5); }
</style>
```

### `frontend/components/LoginModal.vue` (new)

- Full-screen `fixed inset-0 z-50 bg-black/60 backdrop-blur-sm` overlay
- Centered card with the same conic-gradient border treatment as the metric cards
- Username + password inputs, "Sign in" button, error slot
- Submit reuses `useApi().login()` → `auth.setSession()` → `loginModal.hide()` → if `returnTo`, `navigateTo(returnTo)`
- Close via ESC key, clicking overlay outside card, or explicit ✕ button
- `<Teleport to="body">` so it overlays the entire app, including sidebar

### `frontend/app.vue`

Add at the very end (outside `<NuxtPage>`):

```vue
<LoginModal v-if="loginModalStore.open" />
```

Modal renders once, globally, controlled by the store.

## Data Flow

| Flow | Sequence |
|---|---|
| Cold mount, anonymous | `GET /` → guard redirects to `/dashboard` → page mounts → `Promise.allSettled([stats, top])` → numbers count up, cards fade in. |
| Refresh button | `loading=true` → re-fetch both → numbers animate from current → new value. |
| Ticker card click (anonymous) | `e.preventDefault()` → `loginModal.show("/decisions/...")` → modal opens → success → `navigateTo("/decisions/...")`. |
| Sidebar locked-tab click | `loginModal.show(item.to)` → modal → success → navigate. |
| Deep link `/tasks` (anonymous) | Guard → `/dashboard?login=1&return_to=/tasks` → dashboard loads → on mount, reads query → `loginModal.show("/tasks")`. |
| Logged-in user opens `/dashboard` | Renders normally; sidebar's Dashboard tab is active. |

## Error Handling

| Scenario | Behaviour |
|---|---|
| `/api/dashboard/stats` fails | Both metric cards show `—` and an amber "data unavailable" line; Refresh still works. |
| `/api/dashboard/top` fails (5xx) | Top 20 section shows a red error card with the backend `detail` and a Retry button. Stats section unaffected. |
| `get_top_tickers()` Wikipedia fetch fails | Backend returns 503 with `detail`; frontend's top-error branch triggers. |
| A ticker has no decision row | Card renders with placeholder dashes, `opacity-60`, no click handler, no glow. |
| Login modal — bad credentials | Modal stays open, shows red `Invalid credentials` message inline. |
| Login modal — network error | Same modal, generic error message. |
| Logged-in user lands on `/dashboard?login=1` | The `?login=1` is ignored (guard branch checks `!auth.isAuthenticated`); modal does not open. |
| `useCountUp` target changes mid-animation | Animation restarts from the current displayed value, easing to the new target. |

## Testing

### Backend (pytest)

| Test | Verifies |
|---|---|
| `test_count_distinct_dates` | Empty DB → 0; 3 rows on 2 dates → 2. |
| `test_count_distinct_tickers` | Empty DB → 0; 3 rows for 2 tickers → 2. |
| `test_get_latest_decision_per_ticker_returns_latest_per_ticker` | When `AAPL` has rows on 2 dates, the result holds the row with the larger `trade_date`. |
| `test_get_latest_decision_per_ticker_excludes_unknown_tickers` | Asking for `["AAPL", "NOPE"]` returns only `AAPL`. |
| `test_get_latest_decision_per_ticker_handles_empty_list` | Empty input → `{}`, no SQL executed. |
| `test_get_latest_decision_per_ticker_ticker_case_insensitive` | Lowercase input still matches uppercase-stored rows. |
| `test_api_dashboard_stats_returns_counts` | 200 + `{dates_analyzed, tickers_analyzed}` matching seeded data. |
| `test_api_dashboard_stats_is_public_no_token_needed` | No `Authorization` header → 200 (proves `_PUBLIC_PATHS` was extended). |
| `test_api_dashboard_top_returns_ordered_cards_with_rank` | Cards in market-cap order; each has `market_cap_rank` 1..20. |
| `test_api_dashboard_top_marks_tickers_without_decisions` | One mocked top ticker has no row → that card has `no_decision: true`. |
| `test_api_dashboard_top_503_when_wikipedia_fails` | `get_top_tickers` raises → 503 with `detail` body. |
| `test_api_dashboard_top_is_public_no_token_needed` | No header → 200. |

`get_top_tickers` is monkeypatched in tests to return a deterministic list (`["AAPL", "MSFT", ...]`) — no real Wikipedia hit.

### Frontend

No frontend test infra in the repo. Manual checklist:

- [ ] Anonymous `GET /` → arrives at `/dashboard` with full UI rendered.
- [ ] Numbers animate from 0 to target.
- [ ] Conic-border, scanline, hex-grid background all visible and smooth.
- [ ] Top 20 grid is 5 columns on `lg+`, 3 on `md`, 2 on `sm`, 1 below.
- [ ] Rating badges glow per the palette (Buy=emerald, Sell=red, etc.).
- [ ] Card with no decision is dimmed, no hover effects, not clickable.
- [ ] Click any locked sidebar tab → modal opens with title relevant to target.
- [ ] Submit valid credentials → modal closes, navigates to the target tab.
- [ ] Submit bad credentials → red error in modal, modal stays open.
- [ ] ESC closes modal; clicking outside the card closes modal.
- [ ] Anonymous deep link `/tasks` → lands on `/dashboard?login=1&return_to=%2Ftasks`, modal opens; submit success → on `/tasks`.
- [ ] Logged-in user `/dashboard` → no `?login` query handling, no spurious modal.
- [ ] Logged-in user `/` → still sees decisions list (no regression).
- [ ] Refresh button → numbers smoothly transition from current to new value (not 0 → new).
- [ ] Stop backend → stats card shows `—` + amber message; Top 20 section shows red error + Retry.
- [ ] Block Wikipedia in backend → stats works, Top 20 errors with backend detail message.

## Files Touched

```
app/auth.py                                                    (4 line change in _PUBLIC_PATHS)
app/db.py                                                      (add 3 helper functions)
app/api.py                                                     (add 2 routes + 1 private helper)
frontend/middleware/auth.global.ts                             (rewrite)
frontend/composables/useApi.ts                                 (add 2 types + 2 methods)
frontend/composables/useCountUp.ts                             (new)
frontend/stores/loginModal.ts                                  (new)
frontend/components/AppSidebar.vue                             (anonymous branch + locked-button rendering)
frontend/components/DashboardBackground.vue                    (new)
frontend/components/DashboardMetricCard.vue                    (new)
frontend/components/DashboardTickerCard.vue                    (new)
frontend/components/LoginModal.vue                             (new)
frontend/pages/dashboard.vue                                   (new)
frontend/app.vue                                               (mount <LoginModal />)
tests/test_dashboard_db.py                                     (new)
tests/test_dashboard_api.py                                    (new)
```

## Open Questions

None.
