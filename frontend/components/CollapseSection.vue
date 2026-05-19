<template>
  <div class="card overflow-hidden">
    <button
      type="button"
      class="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-gonka-text transition hover:bg-gonka-surface"
      @click="open = !open"
    >
      <span class="flex items-center gap-2">
        <slot name="icon" />
        <span>{{ title }}</span>
      </span>
      <svg
        viewBox="0 0 24 24"
        class="h-4 w-4 text-gonka-muted transition"
        :class="{ 'rotate-180': open }"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
    <div v-show="open" class="border-t border-gonka-border px-4 py-4">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ title: string; defaultOpen?: boolean }>();
const open = ref(props.defaultOpen ?? false);
</script>
