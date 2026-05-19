<template>
  <span
    class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ring-1 ring-inset"
    :class="classes"
  >
    {{ label }}
  </span>
</template>

<script setup lang="ts">
const props = defineProps<{ rating: string | null | undefined }>();

const PALETTE: Record<string, string> = {
  Buy: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  Overweight: "bg-lime-500/15 text-lime-300 ring-lime-500/30",
  Hold: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  Underweight: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  Sell: "bg-red-500/15 text-red-300 ring-red-500/30",
};

const label = computed(() => props.rating || "—");

const classes = computed(() => {
  if (!props.rating) return "bg-gonka-surface text-gonka-muted ring-gonka-border";
  return PALETTE[props.rating] || "bg-gonka-surface text-gonka-muted ring-gonka-border";
});
</script>
