<template>
  <article class="card overflow-hidden">
    <div class="flex flex-wrap items-center gap-3 px-5 py-3">
      <div class="flex items-center gap-2 font-mono text-sm text-white">
        <span class="inline-flex h-2 w-2 animate-pulse rounded-full bg-emerald-500"></span>
        PID {{ task.pid }}
      </div>
      <span
        class="inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide"
        :class="
          task.kind === 'scheduled'
            ? 'border-indigo-500/40 bg-indigo-500/10 text-indigo-200'
            : task.kind === 'orphan'
              ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
              : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
        "
      >
        {{ task.kind === "orphan" ? "auto-detected" : task.kind || "manual" }}
      </span>
      <span class="chip">{{ task.mode ? task.mode.toUpperCase() : "—" }}</span>
      <span class="chip font-mono">{{ shortModel }}</span>
      <span class="chip">{{ task.tickers.length }} tickers</span>
      <span class="chip">workers={{ task.workers }}</span>
      <span class="ml-auto font-mono text-xs text-gonka-muted">
        ⏱ {{ formatElapsed(task.elapsed_seconds) }}
      </span>
      <button class="btn btn-danger" type="button" @click="$emit('stop', task.pid)">Stop</button>
    </div>

    <div class="border-t border-gonka-border px-5 py-3 text-xs text-gonka-muted">
      <div class="truncate font-mono">{{ task.tickers.join(", ") }}</div>
    </div>

    <div class="border-t border-gonka-border">
      <button
        class="flex w-full items-center justify-between px-5 py-2 text-xs font-medium text-gonka-muted transition hover:bg-gonka-surface"
        type="button"
        @click="toggleLog"
      >
        <span>Log tail (last {{ tailLines }} lines)</span>
        <span class="font-mono">{{ logOpen ? "▾" : "▸" }}</span>
      </button>
      <pre
        v-if="logOpen"
        class="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t border-gonka-border bg-[#05080F] px-4 py-3 font-mono text-[11px] text-gonka-text"
      >{{ logText || "(no output yet — runner still bootstrapping)" }}</pre>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { ActiveTask } from "~/composables/useApi";

const props = defineProps<{ task: ActiveTask }>();
defineEmits<{ (e: "stop", pid: number): void }>();

const api = useApi();
const logOpen = ref(false);
const logText = ref("");
const tailLines = 30;
let timer: ReturnType<typeof setInterval> | null = null;

const shortModel = computed(() => {
  const m = props.task.deep_model;
  if (!m) return "?";
  const tail = m.split("/").pop() || m;
  return tail.length > 22 ? tail.slice(0, 22) + "…" : tail;
});

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

async function fetchLog() {
  try {
    const res = await api.readLog(props.task.pid, tailLines);
    logText.value = res.log;
  } catch {
    /* ignore — task may have just finished */
  }
}

function toggleLog() {
  logOpen.value = !logOpen.value;
  if (logOpen.value) {
    fetchLog();
    if (!timer) {
      timer = setInterval(fetchLog, 4000);
    }
  } else if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});
</script>
