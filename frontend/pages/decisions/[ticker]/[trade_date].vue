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
  router.push("/");
}

onMounted(load);
watch([ticker, tradeDate], load);
</script>
