<template>
  <header
    class="sticky top-0 z-20 flex items-center gap-4 border-b border-gonka-border bg-gonka-bg/80 px-6 py-3 backdrop-blur"
  >
    <button
      class="btn btn-ghost lg:hidden"
      type="button"
      aria-label="Toggle sidebar"
      @click="$emit('toggle')"
    >
      <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M4 7h16M4 12h16M4 17h16" stroke-linecap="round" />
      </svg>
    </button>

    <div class="flex flex-1 items-center gap-3">
      <h1 class="text-base font-semibold tracking-tight text-gonka-text">
        {{ pageTitle }}
      </h1>
      <span class="chip">{{ subtitle }}</span>
    </div>

    <div class="hidden items-center gap-2 md:flex">
      <div
        class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
      >
        <span class="font-mono text-gonka-muted">mode</span>
        <span class="font-semibold uppercase">{{ info?.mode || "?" }}</span>
        <span
          class="ml-1 h-2 w-2 rounded-full"
          :class="info?.configured ? 'bg-emerald-500' : 'bg-amber-500'"
        ></span>
      </div>
      <div
        class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
      >
        <span class="font-mono text-gonka-muted">model</span>
        <span class="font-mono">{{ shortModel }}</span>
      </div>
      <div
        class="flex items-center gap-2 rounded-lg border border-gonka-border bg-gonka-surface px-3 py-1.5 text-xs"
      >
        <span class="font-mono text-gonka-muted">workers</span>
        <span class="font-mono">{{ info?.max_workers ?? "?" }}</span>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
defineEmits(["toggle"]);

const route = useRoute();
const infoStore = useInfoStore();
const info = computed(() => infoStore.info);

const pageTitle = computed(() => {
  if (route.path === "/") return "Decisions";
  if (route.path.startsWith("/tasks")) return "Tasks";
  if (route.path.startsWith("/settings")) return "Settings";
  return "Dashboard";
});

const subtitle = computed(() => {
  if (route.path === "/") return "Latest agent verdicts";
  if (route.path.startsWith("/tasks")) return "Runs & schedules";
  if (route.path.startsWith("/settings")) return "Gonka connection";
  return "";
});

const shortModel = computed(() => {
  const m = info.value?.deep_model;
  if (!m) return "—";
  return m.split("/").pop() || m;
});
</script>
