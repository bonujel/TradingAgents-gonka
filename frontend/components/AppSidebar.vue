<template>
  <aside
    class="sticky top-0 hidden h-screen w-64 shrink-0 border-r border-gonka-border bg-gonka-surface lg:flex lg:flex-col"
  >
    <div class="flex items-center gap-3 border-b border-gonka-border px-6 py-5">
      <span
        class="grid h-9 w-9 place-items-center rounded-lg bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/40"
      >
        <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M3 17l6-6 4 4 8-8" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M14 7h7v7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <div class="leading-tight">
        <div class="text-sm font-semibold text-gonka-text">TradingAgents</div>
        <div class="text-[11px] uppercase tracking-wider text-gonka-muted">on Gonka</div>
      </div>
    </div>

    <nav class="flex-1 space-y-1 px-3 py-4 text-sm">
      <NuxtLink
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        class="group flex items-center gap-3 rounded-lg px-3 py-2 text-gonka-muted transition hover:bg-gonka-card hover:text-gonka-text"
        active-class="bg-gonka-card text-gonka-text shadow-card"
      >
        <span
          class="grid h-7 w-7 place-items-center rounded-md border border-gonka-border bg-gonka-card text-gonka-muted group-hover:text-emerald-400"
        >
          <component :is="item.icon" class="h-4 w-4" />
        </span>
        <span class="flex-1">{{ item.label }}</span>
        <span v-if="item.badge" class="chip text-[10px]">{{ item.badge }}</span>
      </NuxtLink>
    </nav>

    <div class="m-3 rounded-lg border border-gonka-border bg-gonka-card p-3 text-xs text-gonka-muted">
      <div class="mb-1 flex items-center justify-between">
        <span class="font-semibold text-gonka-text">Connection</span>
        <span
          class="inline-flex h-2 w-2 rounded-full"
          :class="info?.configured ? 'bg-emerald-500' : 'bg-amber-500'"
        ></span>
      </div>
      <div class="font-mono text-[11px] text-gonka-muted">
        {{ info?.mode?.toUpperCase() || "—" }}
        <span v-if="info?.configured" class="text-emerald-400">· ready</span>
        <span v-else class="text-amber-400">· not configured</span>
      </div>
      <div class="mt-1 truncate font-mono text-[11px]">
        {{ shortModel }}
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { h } from "vue";

const infoStore = useInfoStore();
const info = computed(() => infoStore.info);

const shortModel = computed(() => {
  const m = info.value?.deep_model;
  if (!m) return "no model";
  return m.split("/").pop() || m;
});

const items = [
  {
    to: "/",
    label: "Decisions",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("path", {
            d: "M3 3v18h18",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
          }),
          h("path", {
            d: "M7 14l3-3 3 3 5-6",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
          }),
        ]
      ),
  },
  {
    to: "/tasks",
    label: "Tasks",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "9" }),
          h("path", {
            d: "M10 8l5 4-5 4V8z",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            fill: "currentColor",
          }),
        ]
      ),
  },
  {
    to: "/schedule",
    label: "Schedule",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "9" }),
          h("path", { d: "M12 7v5l3 2", "stroke-linecap": "round", "stroke-linejoin": "round" }),
        ]
      ),
  },
  {
    to: "/settings",
    label: "Settings",
    icon: () =>
      h(
        "svg",
        { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8 },
        [
          h("circle", { cx: "12", cy: "12", r: "3" }),
          h("path", {
            d: "M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.01a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.01a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.56 1.03z",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
          }),
        ]
      ),
  },
];
</script>
