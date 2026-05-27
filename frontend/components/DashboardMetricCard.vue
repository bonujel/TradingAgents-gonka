<template>
  <div class="metric-card relative overflow-hidden rounded-xl p-6">
    <div class="conic-border" aria-hidden="true" />
    <div class="relative">
      <div class="flex items-center gap-2 text-xs uppercase tracking-widest text-gonka-muted">
        <span class="inline-block h-3 w-1 rounded-sm bg-emerald-400" />
        {{ label }}
      </div>
      <div v-if="error" class="mt-6 font-mono text-6xl tracking-widest text-gonka-muted">
        —
      </div>
      <div
        v-else
        class="mt-6 font-mono text-6xl font-bold tracking-widest text-emerald-300"
        :style="{ textShadow: '0 0 24px rgba(34, 197, 94, 0.45)' }"
      >
        {{ animated.toLocaleString() }}
      </div>
      <div v-if="error" class="mt-2 text-xs text-amber-400">{{ error }}</div>
      <div v-else class="mt-2 h-3" />
      <div class="scanline mt-4" aria-hidden="true" />
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  label: string;
  value: number;
  error?: string | null;
}>();

const target = computed(() => props.value);
const animated = useCountUp(target);
</script>

<style scoped>
.metric-card {
  background: linear-gradient(135deg, rgba(14, 19, 28, 0.9), rgba(18, 24, 38, 0.9));
}
.conic-border {
  position: absolute;
  inset: 0;
  background: conic-gradient(from 0deg, #22c55e, #06b6d4, #22c55e);
  border-radius: 0.75rem;
  animation: spin 8s linear infinite;
  z-index: 0;
  opacity: 0.35;
  padding: 1px;
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask-composite: exclude;
}
.scanline {
  height: 2px;
  background: linear-gradient(90deg, transparent, #22c55e, transparent);
  animation: sweep 2.5s ease-in-out infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes sweep {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}
</style>
