<template>
  <div class="flex min-h-screen items-center justify-center bg-gonka-bg px-4">
    <div class="w-full max-w-sm">
      <div class="mb-8 flex flex-col items-center text-center">
        <span
          class="mb-3 grid h-12 w-12 place-items-center rounded-xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/40"
        >
          <svg viewBox="0 0 24 24" class="h-6 w-6" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 17l6-6 4 4 8-8" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M14 7h7v7" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </span>
        <h1 class="text-lg font-semibold text-white">TradingAgents · Gonka</h1>
        <p class="mt-1 text-xs text-gonka-muted">Sign in to the operator console</p>
      </div>

      <form class="card space-y-4 p-6" @submit.prevent="submit">
        <div>
          <label class="label mb-1" for="username">Username</label>
          <input
            id="username"
            v-model="username"
            type="text"
            class="input"
            placeholder="sa or your email"
            autocomplete="username"
            :disabled="loading"
          />
        </div>
        <div>
          <label class="label mb-1" for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="input"
            placeholder="••••••••"
            autocomplete="current-password"
            :disabled="loading"
          />
        </div>

        <p v-if="error" class="text-xs text-red-300">{{ error }}</p>

        <button
          class="btn btn-primary w-full"
          type="submit"
          :disabled="loading || !username || !password"
        >
          {{ loading ? "Signing in…" : "Sign in" }}
        </button>
      </form>

      <p class="mt-6 text-center text-xs text-gonka-muted">
        Lost access? Ask the super admin to reset your password.
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
definePageMeta({ layout: false });

const api = useApi();
const auth = useAuthStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

function errorMessage(e: unknown): string {
  const detail = (e as { data?: { detail?: string } })?.data?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await api.login(username.value.trim(), password.value);
    auth.setSession(res.token, res.username, res.role);
    await navigateTo("/");
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
</script>
