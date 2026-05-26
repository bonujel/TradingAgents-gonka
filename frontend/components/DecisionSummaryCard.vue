<template>
  <NuxtLink
    :to="`/decisions/${encodeURIComponent(row.ticker)}/${row.trade_date}`"
    class="block rounded-lg border border-gonka-border bg-gonka-surface px-5 py-3 transition hover:border-emerald-500/40 hover:bg-gonka-surface/80"
  >
    <div class="flex flex-wrap items-center gap-3">
      <h3 class="font-mono text-lg font-semibold tracking-tight text-white">
        {{ row.ticker }}
      </h3>
      <RatingBadge :rating="row.rating" />
      <span
        v-if="row.has_error"
        class="inline-flex items-center rounded-md bg-red-500/15 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-red-300 ring-1 ring-inset ring-red-500/30"
      >
        errored
      </span>
      <span class="chip">{{ row.trade_date }}</span>
      <span class="chip">{{ shortModel }}</span>
      <span class="ml-auto text-xs text-gonka-muted">
        stored {{ shortTimestamp(row.created_at) }}
      </span>
    </div>
  </NuxtLink>
</template>

<script setup lang="ts">
import type { DecisionSummaryRow } from "~/composables/useApi";

const props = defineProps<{ row: DecisionSummaryRow }>();

const shortModel = computed(() => {
  const m = props.row.deep_model;
  if (!m) return "—";
  const tail = m.split("/").pop() || m;
  return tail.length > 28 ? tail.slice(0, 28) + "…" : tail;
});

function shortTimestamp(iso: string): string {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 19);
}
</script>
