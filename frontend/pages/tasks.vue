<template>
  <div class="space-y-6">
    <header>
      <h2 class="text-2xl font-semibold tracking-tight text-white">Tasks</h2>
      <p class="mt-1 text-sm text-gonka-muted">
        Launch ad-hoc analysis runs, watch active workers, and review recent batches.
      </p>
    </header>

    <section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <MetricCard label="Mode" :value="modeLabel" hint="Selected on Settings" />
      <MetricCard
        label="Deep model"
        :value="shortDeepModel"
        hint="Used by manager & PM"
      />
      <MetricCard
        label="Manual slots"
        :value="`${manualCount} / ${manualMax}`"
        :hint="manualAtCapacity ? 'Stop a run or wait for a slot' : 'Concurrent manual ceiling'"
      >
        <template #trailing>
          <span
            class="inline-flex h-2 w-2 rounded-full"
            :class="manualAtCapacity ? 'bg-amber-500' : 'bg-emerald-500'"
          ></span>
        </template>
      </MetricCard>
      <MetricCard
        label="Scheduled slots"
        :value="`${scheduledCount} / ${scheduledMax}`"
        hint="Independent of manual capacity"
      >
        <template #trailing>
          <span
            class="inline-flex h-2 w-2 rounded-full"
            :class="scheduledCount > 0 ? 'bg-emerald-500' : 'bg-gonka-borderStrong'"
          ></span>
        </template>
      </MetricCard>
    </section>

    <section
      v-if="!info?.configured"
      class="card border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-100"
    >
      Connection mode <span class="font-mono">{{ info?.mode || "?" }}</span> is missing
      credentials. Open
      <NuxtLink to="/settings" class="font-semibold text-amber-300 underline-offset-2 hover:underline"
        >Settings</NuxtLink
      >
      to fill them in before launching a run.
    </section>

    <section class="card space-y-4 p-5">
      <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
        Start new analysis
      </h3>

      <div class="flex flex-wrap gap-2">
        <button class="btn" type="button" @click="loadTopTickers">Top 20 S&P 500</button>
        <button class="btn" type="button" @click="loadSp100" :disabled="loadingTickers">
          {{ loadingTickers ? "Loading…" : "S&P 100" }}
        </button>
        <button class="btn" type="button" @click="loadFullSp500" :disabled="loadingTickers">
          {{ loadingTickers ? "Loading…" : "Full S&P 500" }}
        </button>
        <button class="btn btn-ghost" type="button" @click="tickersInput = ''">Clear</button>
        <span class="ml-auto self-center text-xs text-gonka-muted">
          {{ tickerCount }} tickers
        </span>
      </div>

      <div>
        <label class="label mb-1">Tickers (comma- or whitespace-separated)</label>
        <textarea
          v-model="tickersInput"
          rows="4"
          class="input font-mono text-xs"
          placeholder="NVDA, AAPL, MSFT…"
        />
      </div>

      <div class="grid items-end gap-3 sm:grid-cols-3">
        <div>
          <label class="label mb-1">Workers</label>
          <input
            v-model.number="workers"
            type="number"
            min="1"
            max="16"
            class="input font-mono"
          />
        </div>
        <div class="sm:col-span-2 flex justify-end gap-2">
          <button
            class="btn btn-primary"
            :disabled="
              !info?.configured ||
              launching ||
              tickerCount === 0 ||
              manualAtCapacity
            "
            type="button"
            @click="launch"
          >
            <span v-if="launching">Launching…</span>
            <span v-else-if="manualAtCapacity">Manual capacity full</span>
            <span v-else>▶ Run analysis</span>
          </button>
        </div>
      </div>

      <p v-if="error" class="text-xs text-red-300">{{ error }}</p>
      <p v-if="success" class="text-xs text-emerald-300">{{ success }}</p>
    </section>

    <section>
      <div class="mb-3 flex items-end justify-between">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Active runs · {{ active.length }}
        </h3>
        <div class="flex items-center gap-2 text-xs text-gonka-muted">
          <label class="flex items-center gap-1">
            <input v-model="autoRefresh" type="checkbox" class="accent-emerald-500" />
            Auto-refresh
          </label>
          <button class="btn btn-ghost" type="button" @click="reloadActive">Refresh</button>
        </div>
      </div>

      <EmptyState
        v-if="!active.length"
        title="No active tasks"
        description="Launched runs appear here in real time. Use the form above to start one."
      />

      <div v-else class="space-y-3">
        <ActiveRunRow
          v-for="task in active"
          :key="task.pid"
          :task="task"
          @stop="stopRun"
        />
      </div>
    </section>

    <section>
      <div class="mb-3 flex items-end justify-between">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Recent runs
        </h3>
        <button class="btn btn-ghost" type="button" @click="reloadRecent">Refresh</button>
      </div>
      <EmptyState
        v-if="!recent.length"
        title="No runs recorded yet"
        description="The run history table populates after the first launch completes."
      />
      <div v-else class="card divide-y divide-gonka-border">
        <article
          v-for="run in recent"
          :key="run.id"
          class="grid grid-cols-1 gap-2 px-4 py-3 sm:grid-cols-12 sm:items-center"
        >
          <div class="sm:col-span-2">
            <div class="font-mono text-sm text-white">#{{ run.id }}</div>
            <div class="text-xs text-gonka-muted">{{ run.run_date }}</div>
          </div>
          <div class="sm:col-span-3 text-xs text-gonka-muted">
            {{ shortTime(run.started_at) }}
            <span class="mx-1 text-gonka-border">→</span>
            {{ run.finished_at ? shortTime(run.finished_at) : "…" }}
            <span v-if="run.elapsed_seconds" class="ml-2 font-mono text-gonka-muted"
              >{{ Math.floor(run.elapsed_seconds / 60) }}m{{ run.elapsed_seconds % 60 }}s</span
            >
          </div>
          <div class="sm:col-span-2">
            <span class="inline-flex items-center gap-2 font-mono text-xs">
              <span class="text-emerald-300">{{ run.success_count }}✓</span>
              <span class="text-red-300">{{ run.failure_count }}✗</span>
            </span>
          </div>
          <div class="sm:col-span-5 truncate font-mono text-xs text-gonka-muted">
            {{ run.tickers.join(", ") || "—" }}
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { ActiveTask, RunRecord } from "~/composables/useApi";

const api = useApi();
const infoStore = useInfoStore();

const info = computed(() => infoStore.info);
const modeLabel = computed(() => (info.value?.mode || "?").toUpperCase());
const shortDeepModel = computed(() => {
  const m = info.value?.deep_model;
  if (!m) return "?";
  const tail = m.split("/").pop() || m;
  return tail.length > 22 ? tail.slice(0, 22) + "…" : tail;
});

const tickersInput = ref("");
const workers = ref(4);
const launching = ref(false);
const loadingTickers = ref(false);
const error = ref("");
const success = ref("");

const active = ref<ActiveTask[]>([]);
const recent = ref<RunRecord[]>([]);
const autoRefresh = ref(true);

const manualCount = computed(
  () => active.value.filter((t) => t.kind === "manual").length
);
const scheduledCount = computed(
  () => active.value.filter((t) => t.kind === "scheduled").length
);
const manualMax = computed(() => info.value?.capacity?.manual?.max ?? 2);
const scheduledMax = computed(() => info.value?.capacity?.scheduled?.max ?? 1);
const manualAtCapacity = computed(() => manualCount.value >= manualMax.value);

let pollTimer: ReturnType<typeof setInterval> | null = null;

const tickerCount = computed(() => parseTickers(tickersInput.value).length);

function parseTickers(raw: string): string[] {
  return Array.from(
    new Set(
      raw
        .split(/[\s,]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean)
    )
  );
}

const { formatLocal } = useFormatTime();
function shortTime(iso: string | null) {
  return formatLocal(iso);
}

async function loadTopTickers() {
  loadingTickers.value = true;
  try {
    const t = await api.topTickers(20);
    tickersInput.value = t.join(", ");
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loadingTickers.value = false;
  }
}

async function loadSp100() {
  loadingTickers.value = true;
  try {
    const t = await api.sp100Tickers();
    tickersInput.value = t.join(", ");
    success.value = `Loaded ${t.length} S&P 100 tickers.`;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loadingTickers.value = false;
  }
}

async function loadFullSp500() {
  loadingTickers.value = true;
  try {
    const t = await api.sp500Tickers();
    tickersInput.value = t.join(", ");
    success.value = `Loaded ${t.length} S&P 500 tickers.`;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loadingTickers.value = false;
  }
}

async function launch() {
  error.value = "";
  success.value = "";
  const tickers = parseTickers(tickersInput.value);
  if (!tickers.length) {
    error.value = "No tickers specified.";
    return;
  }
  launching.value = true;
  try {
    const task = await api.startRun({ tickers, workers: workers.value });
    success.value = `Started PID ${task.pid} on ${tickers.length} tickers.`;
    await reloadActive();
  } catch (e: unknown) {
    error.value = errorMessage(e);
  } finally {
    launching.value = false;
  }
}

function errorMessage(e: unknown): string {
  if (typeof e === "object" && e !== null && "data" in e) {
    const data = (e as { data?: { detail?: string } }).data;
    if (data?.detail) return data.detail;
  }
  return e instanceof Error ? e.message : String(e);
}

async function stopRun(pid: number) {
  try {
    await api.stopRun(pid);
    success.value = `Sent SIGTERM to PID ${pid}.`;
    await reloadActive();
  } catch (e: unknown) {
    error.value = errorMessage(e);
  }
}

async function reloadActive() {
  try {
    active.value = await api.listActiveRuns();
  } catch {
    /* ignore transient errors during polling */
  }
}

async function reloadRecent() {
  try {
    recent.value = await api.listRecentRuns(10);
  } catch {
    /* ignore */
  }
}

watch(autoRefresh, (on) => {
  if (on) startPolling();
  else stopPolling();
});

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    await reloadActive();
    if (active.value.length === 0) {
      await reloadRecent();
    }
  }, 4000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(async () => {
  if (!infoStore.loaded) await infoStore.refresh();
  await loadTopTickers();
  workers.value = info.value?.max_workers ?? 4;
  await Promise.all([reloadActive(), reloadRecent()]);
  if (autoRefresh.value) startPolling();
});

onBeforeUnmount(stopPolling);
</script>
