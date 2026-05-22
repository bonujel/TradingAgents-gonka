<template>
  <div class="space-y-6">
    <header>
      <h2 class="text-2xl font-semibold tracking-tight text-white">Personal Settings</h2>
      <p class="mt-1 text-sm text-gonka-muted">
        Manage your own account.
      </p>
    </header>

    <section class="card p-5">
      <div class="flex items-center gap-3">
        <span
          class="grid h-11 w-11 place-items-center rounded-full bg-emerald-500/15 text-sm font-semibold uppercase text-emerald-300 ring-1 ring-emerald-500/30"
        >
          {{ initials }}
        </span>
        <div>
          <div class="font-mono text-sm text-white">{{ auth.username }}</div>
          <div class="text-xs uppercase tracking-wider text-gonka-muted">Operator account</div>
        </div>
      </div>
    </section>

    <section class="card space-y-4 p-5">
      <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
        Change password
      </h3>
      <form class="max-w-md space-y-4" @submit.prevent="submit">
        <div>
          <label class="label mb-1" for="old">Current password</label>
          <input
            id="old"
            v-model="oldPassword"
            type="password"
            class="input"
            autocomplete="current-password"
            :disabled="saving"
          />
        </div>
        <div>
          <label class="label mb-1" for="new">New password</label>
          <input
            id="new"
            v-model="newPassword"
            type="password"
            class="input"
            autocomplete="new-password"
            placeholder="At least 6 characters"
            :disabled="saving"
          />
        </div>
        <div>
          <label class="label mb-1" for="confirm">Confirm new password</label>
          <input
            id="confirm"
            v-model="confirmPassword"
            type="password"
            class="input"
            autocomplete="new-password"
            :disabled="saving"
          />
        </div>

        <p v-if="error" class="text-xs text-red-300">{{ error }}</p>
        <p v-if="message" class="text-xs text-emerald-300">{{ message }}</p>

        <button class="btn btn-primary" type="submit" :disabled="saving || !canSubmit">
          {{ saving ? "Saving…" : "Update password" }}
        </button>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
const api = useApi();
const auth = useAuthStore();

const oldPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const saving = ref(false);
const error = ref("");
const message = ref("");

const initials = computed(() => (auth.username || "?").slice(0, 2).toUpperCase());

const canSubmit = computed(
  () =>
    oldPassword.value.length > 0 &&
    newPassword.value.length >= 6 &&
    confirmPassword.value.length > 0
);

function errorMessage(e: unknown): string {
  const detail = (e as { data?: { detail?: string } })?.data?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

async function submit() {
  error.value = "";
  message.value = "";
  if (newPassword.value !== confirmPassword.value) {
    error.value = "New password and confirmation do not match.";
    return;
  }
  if (newPassword.value.length < 6) {
    error.value = "New password must be at least 6 characters.";
    return;
  }
  saving.value = true;
  try {
    await api.changePassword(oldPassword.value, newPassword.value);
    message.value = "Password updated.";
    oldPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
</script>
