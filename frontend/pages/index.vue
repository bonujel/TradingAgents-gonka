<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight text-white">Decisions</h2>
        <p class="mt-1 text-sm text-gonka-muted">
          Latest agent verdicts persisted from completed runs.
        </p>
      </div>
      <button class="btn" :disabled="loading" @click="loadAll">
        <span v-if="loading">Refreshing…</span>
        <span v-else>Refresh</span>
      </button>
    </header>

    <section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <MetricCard label="Trade dates" :value="dates.length" hint="Calendar days on record" />
      <MetricCard label="Showing" :value="filteredRows.length" hint="Rows after filter" />
      <MetricCard
        label="Succeeded"
        :value="successCount"
        hint="Runs that produced a rating"
      />
      <MetricCard
        label="Failed"
        :value="failureCount"
        hint="Runs that errored out"
      />
    </section>

    <section class="card space-y-4 p-5">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label class="label mb-1">Trade date</label>
          <select v-model="selectedDate" class="input" :disabled="!dates.length">
            <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <div>
          <label class="label mb-1">Ticker filter</label>
          <input
            v-model="tickerFilter"
            type="text"
            placeholder="e.g. NVDA"
            class="input uppercase"
          />
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
              <option v-for="n in [10, 20, 50, 100, 250]" :key="n" :value="n">{{ n }}</option>
            </select>
          </div>
          <button
            v-if="tickerFilter || selectedModel"
            class="btn btn-ghost"
            type="button"
            @click="
              tickerFilter = '';
              selectedModel = '';
            "
          >
            Clear
          </button>
        </div>
      </div>
    </section>

    <section v-if="!dates.length && !loading">
      <EmptyState
        title="No decisions yet"
        description="Open the Tasks page to launch a run; the first PM verdict will show up here."
      >
        <template #actions>
          <NuxtLink to="/tasks" class="btn btn-primary">Go to Tasks</NuxtLink>
        </template>
      </EmptyState>
    </section>

    <section v-else-if="!filteredRows.length && !loading">
      <EmptyState title="No matching decisions" description="Try a different ticker or date." />
    </section>

    <section v-else class="space-y-4">
      <DecisionCard v-for="row in pagedRows" :key="`${row.ticker}-${row.trade_date}`" :row="row" />
      <div
        v-if="filteredRows.length > pageSize"
        class="flex items-center justify-between rounded-lg border border-gonka-border bg-gonka-surface px-4 py-3 text-xs text-gonka-muted"
      >
        <span>
          Showing {{ Math.min(pageSize, filteredRows.length) }} of
          {{ filteredRows.length }} matching rows.
        </span>
        <div class="flex items-center gap-2">
          <button
            class="btn btn-ghost"
            :disabled="page === 0"
            type="button"
            @click="page = Math.max(0, page - 1)"
          >
            ← Prev
          </button>
          <span class="font-mono">{{ page + 1 }} / {{ totalPages }}</span>
          <button
            class="btn btn-ghost"
            :disabled="page >= totalPages - 1"
            type="button"
            @click="page = Math.min(totalPages - 1, page + 1)"
          >
            Next →
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { DecisionRow } from "~/composables/useApi";

const api = useApi();

const loading = ref(false);
const dates = ref<string[]>([]);
const selectedDate = ref<string>("");
const rows = ref<DecisionRow[]>([]);
const tickerFilter = ref("");
const selectedModel = ref("");
const pageSize = ref(20);
const page = ref(0);

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

async function loadRows() {
  if (!selectedDate.value) {
    rows.value = [];
    return;
  }
  rows.value = await api.listDecisions({ date: selectedDate.value });
  page.value = 0;
}

async function loadAll() {
  loading.value = true;
  try {
    await loadDates();
    await loadRows();
  } finally {
    loading.value = false;
  }
}

watch(selectedDate, () => loadRows());

// Distinct deep_model values present in the currently-loaded date's rows.
// Sorted alphabetically so the dropdown order is stable when models rotate.
const availableModels = computed(() => {
  const seen = new Set<string>();
  for (const r of rows.value) {
    if (r.deep_model) seen.add(r.deep_model);
  }
  return [...seen].sort();
});

const filteredRows = computed(() => {
  const f = tickerFilter.value.trim().toUpperCase();
  const m = selectedModel.value;
  return rows.value.filter((r) => {
    if (f && !r.ticker.includes(f)) return false;
    if (m && r.deep_model !== m) return false;
    return true;
  });
});

const successCount = computed(() => filteredRows.value.filter((r) => !r.error).length);
const failureCount = computed(() => filteredRows.value.length - successCount.value);

const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value))
);

const pagedRows = computed(() => {
  const start = page.value * pageSize.value;
  return filteredRows.value.slice(start, start + pageSize.value);
});

watch(filteredRows, () => {
  if (page.value >= totalPages.value) {
    page.value = 0;
  }
});

// Drop the selected model if it's no longer present on the new trade date,
// otherwise the user sees zero rows with no visual cue why.
watch(availableModels, (models) => {
  if (selectedModel.value && !models.includes(selectedModel.value)) {
    selectedModel.value = "";
  }
});

onMounted(loadAll);
</script>
