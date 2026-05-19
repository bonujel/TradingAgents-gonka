<template>
  <div class="space-y-6">
    <header>
      <h2 class="text-2xl font-semibold tracking-tight text-white">Schedule</h2>
      <p class="mt-1 text-sm text-gonka-muted">
        At most one schedule. Manual launches stay independent — scheduled and manual
        slots are tracked separately.
      </p>
    </header>

    <section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div class="card px-5 py-4">
        <div class="label">Server time</div>
        <div class="mt-2 font-mono text-xl font-semibold text-white">
          {{ formattedLocalTime }}
        </div>
        <p class="mt-1 text-xs text-gonka-muted">
          {{ serverTimeZoneLine }}
        </p>
      </div>
      <MetricCard
        label="Schedule status"
        :value="schedule?.config.enabled ? 'Enabled' : 'Disabled'"
        :hint="schedule?.next_run ? `Next: ${formatIso(schedule.next_run)}` : 'Toggle below to enable'"
      >
        <template #trailing>
          <span
            class="inline-flex h-2 w-2 rounded-full"
            :class="schedule?.config.enabled ? 'bg-emerald-500' : 'bg-gonka-borderStrong'"
          ></span>
        </template>
      </MetricCard>
      <MetricCard
        label="Scheduled slots"
        :value="`${schedule?.capacity.scheduled.current ?? 0} / ${schedule?.capacity.scheduled.max ?? 1}`"
        hint="One scheduled run may execute at a time"
      />
      <MetricCard
        label="Manual slots"
        :value="`${schedule?.capacity.manual.current ?? 0} / ${schedule?.capacity.manual.max ?? 2}`"
        hint="Up to two manual runs may execute concurrently"
      />
    </section>

    <section class="card space-y-5 p-5">
      <div class="flex items-center justify-between gap-3">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Schedule configuration
        </h3>
        <span
          class="inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide"
          :class="
            schedule?.config.enabled
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
              : 'border-gonka-border bg-gonka-surface text-gonka-muted'
          "
        >
          <span
            class="inline-flex h-2 w-2 rounded-full"
            :class="schedule?.config.enabled ? 'bg-emerald-500' : 'bg-gonka-borderStrong'"
          ></span>
          {{ schedule?.config.enabled ? "Active" : "Inactive" }}
        </span>
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <div>
          <label class="label mb-1">Start hour (0–23)</label>
          <input
            v-model.number="draft.start_hour"
            type="number"
            min="0"
            max="23"
            class="input font-mono"
          />
        </div>
        <div>
          <label class="label mb-1">Start minute (0–59)</label>
          <input
            v-model.number="draft.start_minute"
            type="number"
            min="0"
            max="59"
            class="input font-mono"
          />
        </div>
        <div>
          <label class="label mb-1">Interval (hours)</label>
          <input
            v-model.number="draft.interval_hours"
            type="number"
            min="1"
            max="168"
            class="input font-mono"
          />
        </div>
      </div>

      <p class="text-xs text-gonka-muted">
        First fire is today at
        <span class="font-mono text-emerald-300">
          {{ pad(draft.start_hour) }}:{{ pad(draft.start_minute) }}
        </span>
        in
        <span class="font-mono">{{ schedule?.server_time.tz_name || "local" }}</span>;
        if that's already passed, the scheduler advances by the interval until the
        next slot is in the future. Times are interpreted in the host's timezone
        (shown above).
      </p>

      <div>
        <label class="label mb-1">Workers (scheduled task only)</label>
        <input
          v-model.number="draft.workers"
          type="number"
          min="1"
          max="16"
          class="input font-mono max-w-xs"
        />
        <p class="mt-1 text-xs text-gonka-muted">
          Independent from the Settings → "Manual default workers" value.
        </p>
      </div>

      <div>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <label class="label">Tickers</label>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-ghost" type="button" @click="loadTopTickers">
              Top 20 S&P 500
            </button>
            <button class="btn btn-ghost" type="button" @click="loadFullSp500" :disabled="loadingTickers">
              {{ loadingTickers ? "Loading…" : "Full S&P 500" }}
            </button>
            <button class="btn btn-ghost" type="button" @click="tickersInput = ''">
              Clear
            </button>
            <span class="self-center text-xs text-gonka-muted">{{ tickerCount }} tickers</span>
          </div>
        </div>
        <textarea
          v-model="tickersInput"
          rows="3"
          class="input mt-1 font-mono text-xs"
          placeholder="NVDA, AAPL, MSFT…  (leave blank to use top-20 S&P 500 at fire time)"
        />
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="saveAndEnable">
          {{
            saving
              ? "Saving…"
              : schedule?.config.enabled
                ? "Save changes"
                : "Save & activate"
          }}
        </button>
        <button
          v-if="schedule?.config.enabled"
          class="btn"
          type="button"
          :disabled="saving"
          @click="disableSchedule"
        >
          Pause schedule
        </button>
        <span v-if="message" class="text-xs text-emerald-300">{{ message }}</span>
        <span v-if="error" class="text-xs text-red-300">{{ error }}</span>
        <span class="ml-auto text-xs text-gonka-muted">
          Saving activates the schedule. Pause to keep settings on disk but stop firing.
        </span>
      </div>
    </section>

    <section
      v-if="!info?.configured"
      class="card border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-100"
    >
      Connection mode <span class="font-mono">{{ info?.mode || "?" }}</span> has no
      credentials. The scheduler will skip its fire until you fill them in on
      <NuxtLink to="/settings" class="font-semibold text-amber-300 underline-offset-2 hover:underline">
        Settings</NuxtLink
      >.
    </section>
  </div>
</template>

<script setup lang="ts">
import type { ScheduleConfig, ScheduleResponse } from "~/composables/useApi";

const api = useApi();
const infoStore = useInfoStore();
const info = computed(() => infoStore.info);

const schedule = ref<ScheduleResponse | null>(null);
const tickersInput = ref("");
const loadingTickers = ref(false);
const saving = ref(false);
const message = ref("");
const error = ref("");

// ``enabled`` lives on the saved config, not on the form draft — the
// Save / Pause buttons set it explicitly via ``persist(true|false)``.
const draft = reactive<Omit<ScheduleConfig, "enabled" | "tickers">>({
  start_hour: 16,
  start_minute: 30,
  interval_hours: 24,
  workers: 4,
});

const localNow = ref(new Date());
let ticker: ReturnType<typeof setInterval> | null = null;
let polling: ReturnType<typeof setInterval> | null = null;

const tickerCount = computed(() => parseTickers(tickersInput.value).length);

const formattedLocalTime = computed(() => {
  if (!schedule.value) return "—";
  // Re-anchor on the server's last-reported epoch plus the wall-clock
  // milliseconds that have elapsed since we received it. Then shift by
  // the server's UTC offset so ``toISOString`` (which always emits UTC)
  // ends up rendering the *server's* local wall-clock string. Otherwise
  // the card would silently show UTC even though the label says CST.
  const elapsedMs = localNow.value.getTime() - lastSync.value;
  const offsetSec = schedule.value.server_time.tz_offset_seconds || 0;
  const shifted = new Date(serverEpochMs.value + elapsedMs + offsetSec * 1000);
  return shifted.toISOString().replace("T", " ").slice(0, 19);
});

const serverTimeZoneLine = computed(() => {
  const s = schedule.value;
  if (!s) return "";
  const offsetSec = s.server_time.tz_offset_seconds || 0;
  const hours = Math.abs(offsetSec) / 3600;
  const sign = offsetSec >= 0 ? "+" : "-";
  return `${s.server_time.tz_name} (UTC${sign}${hours})`;
});

const lastSync = ref(0);
const serverEpochMs = ref(0);

function pad(n: number): string {
  return String(Math.max(0, n)).padStart(2, "0");
}

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

function formatIso(iso: string): string {
  return iso.replace("T", " ").slice(0, 19);
}

function errorMessage(e: unknown): string {
  if (typeof e === "object" && e !== null && "data" in e) {
    const data = (e as { data?: { detail?: string } }).data;
    if (data?.detail) return data.detail;
  }
  return e instanceof Error ? e.message : String(e);
}

async function loadTopTickers() {
  loadingTickers.value = true;
  try {
    const t = await api.topTickers(20);
    tickersInput.value = t.join(", ");
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loadingTickers.value = false;
  }
}

async function loadFullSp500() {
  loadingTickers.value = true;
  try {
    const t = await api.sp500Tickers();
    tickersInput.value = t.join(", ");
    message.value = `Loaded ${t.length} S&P 500 tickers.`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loadingTickers.value = false;
  }
}

async function refresh() {
  try {
    const resp = await api.getSchedule();
    schedule.value = resp;
    serverEpochMs.value = new Date(resp.server_time.now).getTime();
    lastSync.value = Date.now();
  } catch {
    /* ignore transient errors */
  }
}

function hydrateDraft() {
  const cfg = schedule.value?.config;
  if (!cfg) return;
  draft.start_hour = cfg.start_hour;
  draft.start_minute = cfg.start_minute;
  draft.interval_hours = cfg.interval_hours;
  draft.workers = cfg.workers;
  tickersInput.value = cfg.tickers.join(", ");
}

async function persist(enabled: boolean) {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    const payload: ScheduleConfig = {
      ...draft,
      enabled,
      tickers: parseTickers(tickersInput.value),
    };
    schedule.value = await api.putSchedule(payload);
    serverEpochMs.value = new Date(schedule.value.server_time.now).getTime();
    lastSync.value = Date.now();
    message.value = enabled
      ? `Activated. Next fire: ${schedule.value.next_run ? formatIso(schedule.value.next_run) : "?"}`
      : "Paused. Settings retained.";
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}

async function saveAndEnable() {
  await persist(true);
}

async function disableSchedule() {
  await persist(false);
}

onMounted(async () => {
  if (!infoStore.loaded) await infoStore.refresh();
  await refresh();
  hydrateDraft();
  ticker = setInterval(() => {
    localNow.value = new Date();
  }, 1000);
  polling = setInterval(refresh, 15000);
});

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker);
  if (polling) clearInterval(polling);
});

// NOTE: do NOT watch ``schedule.value.config`` to re-hydrate the form.
// The 15-second poll for the live clock would otherwise overwrite the
// operator's in-progress edits with the last-saved values. The form is
// only seeded on mount and after an explicit save; concurrent edits
// from another browser tab are intentionally not synced live.
</script>
