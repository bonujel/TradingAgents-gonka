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
        <div class="label">Now (your timezone)</div>
        <div class="mt-2 font-mono text-xl font-semibold text-white">
          {{ formattedLocalNow }}
        </div>
        <p class="mt-1 text-xs text-gonka-muted">
          Server stores everything in UTC; this card and the schedule
          inputs below are in your browser timezone.
        </p>
      </div>
      <MetricCard
        label="Schedule status"
        :value="schedule?.config.enabled ? 'Enabled' : 'Disabled'"
        :hint="schedule?.next_run ? `Next: ${formatLocal(schedule.next_run)}` : 'Toggle below to enable'"
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

    <section
      v-if="schedule?.config.pending_catch_up || schedule?.config.last_start_error"
      class="card space-y-3 border-amber-500/30 bg-amber-500/5 p-4 text-sm"
    >
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div class="font-semibold text-amber-100">
            {{ schedule?.config.pending_catch_up ? "Catch-up pending" : "Schedule attention" }}
          </div>
          <p class="mt-1 text-xs text-amber-100/75">
            Missed scheduled fires are coalesced into one catch-up run. The catch-up
            starts automatically when the scheduled slot is free.
          </p>
        </div>
        <button
          v-if="schedule?.config.pending_catch_up"
          class="btn"
          type="button"
          :disabled="saving"
          @click="clearPending"
        >
          Clear pending
        </button>
      </div>
      <div class="grid gap-2 text-xs text-amber-100/80 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <span class="label">Missed fires</span>
          <div class="mt-1 font-mono text-white">{{ schedule?.config.missed_count ?? 0 }}</div>
        </div>
        <div>
          <span class="label">Last missed</span>
          <div class="mt-1 font-mono text-white">{{ formatMaybeLocal(schedule?.config.last_missed_at) }}</div>
        </div>
        <div>
          <span class="label">Last catch-up</span>
          <div class="mt-1 font-mono text-white">{{ formatMaybeLocal(schedule?.config.last_catch_up_at) }}</div>
        </div>
        <div>
          <span class="label">Last regular fire</span>
          <div class="mt-1 font-mono text-white">{{ formatMaybeLocal(schedule?.config.last_regular_fire_at) }}</div>
        </div>
      </div>
      <p v-if="schedule?.config.last_start_error" class="text-xs text-red-200">
        Last start error: <span class="font-mono">{{ schedule.config.last_start_error }}</span>
      </p>
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
        Enter the hour/minute in <span class="font-mono">your browser timezone</span>.
        First fire is today at
        <span class="font-mono text-emerald-300">
          {{ pad(draft.start_hour) }}:{{ pad(draft.start_minute) }}
        </span>
        local
        (<span class="font-mono">{{ pad(utcStartHour) }}:{{ pad(utcStartMinute) }}</span> UTC,
        the canonical form the server stores). If that local moment is already past today, the
        scheduler advances by the interval until the next slot is in the future.
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
            <button class="btn btn-ghost" type="button" @click="loadSp100" :disabled="loadingTickers">
              {{ loadingTickers ? "Loading…" : "S&P 100" }}
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

// Operator-local wall clock. Backend (now in UTC) is no longer the
// source — every operator just reads their own browser clock. Re-renders
// every second via the ``ticker`` interval set in onMounted().
const formattedLocalNow = computed(() =>
  localNow.value.toLocaleString("sv-SE", { hour12: false })
);

const { formatLocal } = useFormatTime();

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

function formatMaybeLocal(iso?: string | null): string {
  return iso ? formatLocal(iso) : "—";
}

// Convert local (browser-tz) HH:MM into the canonical UTC HH:MM that the
// backend stores. Uses today as the date anchor — the schedule fires at
// a daily UTC moment, so date doesn't matter beyond resolving the offset.
// Handles non-integer-hour tz (e.g. India UTC+5:30) correctly because
// it goes through ``Date`` rather than subtracting ``offset`` by hand.
function localHmToUtc(hour: number, minute: number): { hour: number; minute: number } {
  const d = new Date();
  d.setHours(hour, minute, 0, 0);
  return { hour: d.getUTCHours(), minute: d.getUTCMinutes() };
}

function utcHmToLocal(hour: number, minute: number): { hour: number; minute: number } {
  const d = new Date();
  d.setUTCHours(hour, minute, 0, 0);
  return { hour: d.getHours(), minute: d.getMinutes() };
}

// What the form's local HH:MM is in UTC — surfaced under the form so
// the operator can verify the canonical value before saving.
const utcStartHour = computed(
  () => localHmToUtc(draft.start_hour, draft.start_minute).hour
);
const utcStartMinute = computed(
  () => localHmToUtc(draft.start_hour, draft.start_minute).minute
);

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

async function loadSp100() {
  loadingTickers.value = true;
  try {
    const t = await api.sp100Tickers();
    tickersInput.value = t.join(", ");
    message.value = `Loaded ${t.length} S&P 100 tickers.`;
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
    schedule.value = await api.getSchedule();
  } catch {
    /* ignore transient errors */
  }
}

function hydrateDraft() {
  const cfg = schedule.value?.config;
  if (!cfg) return;
  // Backend stores UTC; the form shows operator-local. Convert here so
  // the user can read/edit familiar wall-clock values.
  const local = utcHmToLocal(cfg.start_hour, cfg.start_minute);
  draft.start_hour = local.hour;
  draft.start_minute = local.minute;
  draft.interval_hours = cfg.interval_hours;
  draft.workers = cfg.workers;
  tickersInput.value = cfg.tickers.join(", ");
}

async function persist(enabled: boolean) {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    // Operator-local → UTC for the wire. Backend treats start_hour /
    // start_minute as UTC integers.
    const utcHm = localHmToUtc(draft.start_hour, draft.start_minute);
    const payload: ScheduleConfig = {
      ...draft,
      start_hour: utcHm.hour,
      start_minute: utcHm.minute,
      enabled,
      tickers: parseTickers(tickersInput.value),
    };
    schedule.value = await api.putSchedule(payload);
    message.value = enabled
      ? `Activated. Next fire: ${schedule.value.next_run ? formatLocal(schedule.value.next_run) : "?"}`
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

async function clearPending() {
  const cfg = schedule.value?.config;
  if (!cfg) return;
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    schedule.value = await api.putSchedule({
      enabled: cfg.enabled,
      start_hour: cfg.start_hour,
      start_minute: cfg.start_minute,
      interval_hours: cfg.interval_hours,
      workers: cfg.workers,
      tickers: cfg.tickers,
      clear_pending: true,
    });
    message.value = "Pending catch-up cleared.";
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
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
