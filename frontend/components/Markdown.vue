<template>
  <div class="prose-report" v-html="rendered" />
</template>

<script setup lang="ts">
import { marked } from "marked";

const props = defineProps<{ source: string | null | undefined }>();

marked.setOptions({ gfm: true, breaks: false });

const rendered = computed(() => {
  if (!props.source) return "";
  try {
    return marked.parse(props.source) as string;
  } catch {
    return `<pre>${escapeHtml(props.source)}</pre>`;
  }
});

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
</script>
