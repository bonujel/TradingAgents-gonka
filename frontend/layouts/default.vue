<template>
  <div class="flex min-h-screen bg-gonka-bg">
    <AppSidebar />

    <div class="flex flex-1 flex-col">
      <AppTopbar />
      <main class="flex-1 overflow-x-hidden p-6 lg:p-8">
        <div class="mx-auto max-w-7xl">
          <slot />
        </div>
      </main>
      <footer class="border-t border-gonka-border px-6 py-3 text-xs text-gonka-muted">
        TradingAgents · Gonka operator console
        <span class="float-right">
          <a
            class="hover:text-gonka-text"
            href="https://gonka.ai"
            target="_blank"
            rel="noopener"
            >gonka.ai</a
          >
          ·
          <a
            class="hover:text-gonka-text"
            href="https://router.gonkascan.com"
            target="_blank"
            rel="noopener"
            >router.gonkascan.com</a
          >
        </span>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
const infoStore = useInfoStore();
const auth = useAuthStore();

onMounted(() => {
  // /api/info requires a token; calling it as an anonymous visitor would
  // 401 and trigger the redirect-to-login fallback. Anonymous visitors on
  // /dashboard don't need this data anyway — the sidebar/topbar widgets
  // that consume infoStore hide their info-dependent chrome below.
  if (auth.isAuthenticated && !infoStore.loaded) {
    infoStore.refresh();
  }
});
</script>
