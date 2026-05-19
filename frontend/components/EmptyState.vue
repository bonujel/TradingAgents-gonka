<template>
  <div class="card flex flex-col items-center justify-center px-6 py-12 text-center">
    <div
      class="mb-3 grid h-12 w-12 place-items-center rounded-full bg-gonka-surface text-gonka-muted ring-1 ring-gonka-border"
    >
      <slot name="icon">
        <svg viewBox="0 0 24 24" class="h-6 w-6" fill="none" stroke="currentColor" stroke-width="1.6">
          <circle cx="12" cy="12" r="9" />
          <path d="M9 12h6M12 9v6" stroke-linecap="round" />
        </svg>
      </slot>
    </div>
    <h3 class="text-sm font-semibold text-gonka-text">{{ title }}</h3>
    <p v-if="description" class="mt-1 max-w-md text-xs text-gonka-muted">
      {{ description }}
    </p>
    <div v-if="$slots.actions" class="mt-4">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; description?: string }>();
</script>
