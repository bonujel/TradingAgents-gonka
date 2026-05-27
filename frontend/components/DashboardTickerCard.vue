<template>
  <NuxtLink
    v-if="isClickable"
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
  <div v-else :class="cardClass">
    <div class="flex items-baseline justify-between gap-2">
      <span class="font-mono text-lg font-bold tracking-tight text-white">{{ card.ticker }}</span>
      <span class="font-mono text-[10px] text-gonka-muted">#{{ card.market_cap_rank }}</span>
    </div>
    <div class="mt-2">
      <div class="font-mono text-xs text-gonka-muted">─ ─ ─</div>
    </div>
    <div class="mt-3 space-y-0.5 text-[10px] text-gonka-muted">
      <div>no analysis yet</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { DashboardCard } from "~/composables/useApi";

const props = defineProps<{ card: DashboardCard }>();

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
