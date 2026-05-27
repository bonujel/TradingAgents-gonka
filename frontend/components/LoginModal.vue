<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm"
      @click.self="close"
      @keydown.esc.window="close"
    >
      <div class="login-modal-card relative w-full max-w-sm rounded-xl p-6">
        <div class="conic-border" aria-hidden="true" />
        <div class="relative">
          <button
            type="button"
            class="absolute right-0 top-0 text-gonka-muted hover:text-gonka-text"
            aria-label="Close"
            @click="close"
          >
            ✕
          </button>
          <div class="mb-6 flex flex-col items-center text-center">
            <span
              class="mb-3 grid h-12 w-12 place-items-center rounded-xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/40"
            >
              <svg viewBox="0 0 24 24" class="h-6 w-6" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M3 17l6-6 4 4 8-8" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M14 7h7v7" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <h2 class="text-base font-semibold text-white">Sign in</h2>
            <p class="mt-1 text-xs text-gonka-muted">
              Log in to access the full operator console
            </p>
          </div>
          <form class="space-y-4" @submit.prevent="submit">
            <div>
              <label class="label mb-1" for="login-modal-username">Username</label>
              <input
                id="login-modal-username"
                v-model="username"
                type="text"
                class="input"
                placeholder="sa or your email"
                autocomplete="username"
                :disabled="loading"
                autofocus
              />
            </div>
            <div>
              <label class="label mb-1" for="login-modal-password">Password</label>
              <input
                id="login-modal-password"
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
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const api = useApi();
const auth = useAuthStore();
const loginModal = useLoginModalStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

function errorMessage(e: unknown): string {
  const detail = (e as { data?: { detail?: string } })?.data?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

function close() {
  if (loading.value) return;
  loginModal.hide();
  username.value = "";
  password.value = "";
  error.value = "";
}

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await api.login(username.value.trim(), password.value);
    auth.setSession(res.token, res.username, res.role);
    const target = loginModal.returnTo;
    loginModal.hide();
    username.value = "";
    password.value = "";
    if (target) await navigateTo(target);
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-modal-card {
  background: linear-gradient(135deg, rgba(14, 19, 28, 0.95), rgba(18, 24, 38, 0.95));
}
.conic-border {
  position: absolute;
  inset: 0;
  background: conic-gradient(from 0deg, #22c55e, #06b6d4, #22c55e);
  border-radius: 0.75rem;
  animation: spin 8s linear infinite;
  z-index: 0;
  opacity: 0.45;
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
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
