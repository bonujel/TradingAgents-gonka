<template>
  <article class="card overflow-hidden">
    <header class="flex flex-wrap items-center gap-3 border-b border-gonka-border px-5 py-4">
      <!-- Left group: ticker · rating · company name · trade date -->
      <div class="flex items-baseline gap-2">
        <h3 class="font-mono text-xl font-semibold tracking-tight text-white">
          {{ row.ticker }}
        </h3>
        <RatingBadge :rating="row.rating" />
      </div>
      <span class="truncate text-sm text-gonka-muted">{{ companyName }}</span>
      <span class="chip">
        {{ row.trade_date }}
      </span>

      <!-- Right group: model · stored timestamp -->
      <span class="ml-auto chip">
        {{ shortModel }}
      </span>
      <span class="text-xs text-gonka-muted">
        stored {{ shortTimestamp(row.created_at) }}
      </span>
    </header>

    <div v-if="row.error" class="border-l-4 border-red-500/60 bg-red-500/5 px-5 py-4">
      <div class="mb-1 text-xs font-semibold uppercase tracking-wider text-red-300">
        Run failed
      </div>
      <pre
        class="overflow-x-auto whitespace-pre-wrap break-words text-xs text-red-200"
      >{{ row.error }}</pre>
    </div>

    <div v-else class="space-y-3 p-5">
      <CollapseSection
        v-if="row.final_decision"
        title="Portfolio Manager decision"
        :default-open="true"
      >
        <Markdown :source="row.final_decision" />
      </CollapseSection>

      <CollapseSection v-if="row.trader_plan" title="Trader plan">
        <Markdown :source="row.trader_plan" />
      </CollapseSection>

      <CollapseSection v-if="row.investment_plan" title="Research manager / debate verdict">
        <Markdown :source="row.investment_plan" />
      </CollapseSection>

      <CollapseSection
        v-if="hasAnalystReports"
        title="Analyst reports"
      >
        <div class="border-b border-gonka-border">
          <nav class="-mb-px flex flex-wrap gap-2">
            <button
              v-for="tab in availableTabs"
              :key="tab.key"
              type="button"
              class="rounded-t-md border-b-2 px-3 py-2 text-xs font-medium transition"
              :class="
                tab.key === currentTab
                  ? 'border-emerald-500 text-emerald-300'
                  : 'border-transparent text-gonka-muted hover:text-gonka-text'
              "
              @click="currentTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </nav>
        </div>
        <div class="pt-3">
          <Markdown :source="currentBody" />
        </div>
      </CollapseSection>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { DecisionRow } from "~/composables/useApi";

const props = defineProps<{ row: DecisionRow }>();

const tickerNames = useTickerNamesStore();
onMounted(() => tickerNames.ensureLoaded());

// Falls back to the ticker itself when the name map hasn't loaded yet
// or the ticker is outside the S&P 500 universe.
const companyName = computed(() => tickerNames.lookup(props.row.ticker));

const analystReports = computed(() => [
  { key: "market", label: "Market", body: props.row.market_report },
  { key: "sentiment", label: "Sentiment", body: props.row.sentiment_report },
  { key: "news", label: "News", body: props.row.news_report },
  { key: "fundamentals", label: "Fundamentals", body: props.row.fundamentals_report },
]);

const availableTabs = computed(() => analystReports.value.filter((t) => !!t.body));
const hasAnalystReports = computed(() => availableTabs.value.length > 0);
const currentTab = ref(availableTabs.value[0]?.key ?? "market");

watch(availableTabs, (tabs) => {
  if (!tabs.find((t) => t.key === currentTab.value)) {
    currentTab.value = tabs[0]?.key ?? "market";
  }
});

const currentBody = computed(
  () => availableTabs.value.find((t) => t.key === currentTab.value)?.body ?? ""
);

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
