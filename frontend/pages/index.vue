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
          <select v-model="selectedModel" class="input">
            <option value="">All models</option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>
          <p v-if="modelsError" class="mt-1 text-xs text-amber-400">
            Could not load model list ({{ modelsError }}).
          </p>
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
const modelsError = ref<string | null>(null);

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
    modelsError.value = null;
    return;
  }
  modelsError.value = null;
  try {
    availableModels.value = await api.listDecisionModels(selectedDate.value);
  } catch (err: unknown) {
    const e = err as { data?: { detail?: string }; message?: string };
    modelsError.value = e?.data?.detail || e?.message || "fetch failed";
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

// ─── Page-reset helper ───────────────────────────────────────────────────
// Setting page.value = 1 normally triggers watch(page) → loadRows().
// When a filter change already calls loadRows() directly, we use this flag
// to suppress the redundant page-watcher fetch.

let suppressPageWatch = false;
function setPageWithoutFetch(p: number) {
  if (page.value === p) return;
  suppressPageWatch = true;
  page.value = p;
  // suppressPageWatch is cleared inside the page watcher
}

// ─── Watchers ────────────────────────────────────────────────────────────

watch(page, () => {
  if (suppressPageWatch) {
    suppressPageWatch = false;
    return;
  }
  pushQuery();
  loadRows();
});

// Non-debounced filters: snap to page 1, then fetch once.
watch([selectedRating, selectedModel, pageSize], () => {
  if (page.value !== 1) {
    setPageWithoutFetch(1);
  }
  pushQuery();
  loadRows();
});

// Debounced ticker filter (300 ms) — avoids a fetch per keystroke.
let tickerDebounce: ReturnType<typeof setTimeout> | null = null;
watch(tickerFilter, () => {
  if (tickerDebounce) clearTimeout(tickerDebounce);
  tickerDebounce = setTimeout(() => {
    if (page.value !== 1) {
      setPageWithoutFetch(1);
    }
    pushQuery();
    loadRows();
  }, 300);
});

watch(selectedDate, async () => {
  if (page.value !== 1) {
    setPageWithoutFetch(1);
  }
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

async function reloadAll() {
  // Track whether selectedDate was already set (e.g. restored from URL query).
  // loadDates() may set selectedDate from "" → something, which fires
  // watch(selectedDate) → loadModels + loadRows. If selectedDate was already
  // set before loadDates(), or if no dates exist, we must trigger manually.
  const hadDateBefore = !!selectedDate.value;
  await loadDates();
  // If loadDates set a new date, the watcher handles the downstream fetches.
  // If selectedDate was already populated (URL restore) or no dates exist at
  // all, the watcher will not fire, so we fetch here.
  if (hadDateBefore || !selectedDate.value) {
    if (selectedDate.value) {
      await loadModels();
      await loadRows();
    }
  }
}

onMounted(reloadAll);
</script>
