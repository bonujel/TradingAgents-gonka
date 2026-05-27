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
    const raw = typeof route.query.return_to === "string" ? route.query.return_to : null;
    // Only accept genuinely-relative paths. Reject absolute URLs and
    // protocol-relative (`//evil.com`) forms — they'd let an attacker
    // craft a post-login redirect to an external site.
    const returnTo = raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : null;
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
