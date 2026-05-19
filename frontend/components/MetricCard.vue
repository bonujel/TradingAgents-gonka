<template>
  <div class="card px-5 py-4">
    <div class="flex items-center justify-between">
      <span class="label">{{ label }}</span>
      <slot name="trailing" />
    </div>
    <div class="mt-2 font-mono text-2xl font-semibold text-white">
      <slot>{{ value }}</slot>
    </div>
    <p v-if="hint" class="mt-1 text-xs text-gonka-muted">{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string;
  value?: string | number | null;
  hint?: string;
}>();
</script>
