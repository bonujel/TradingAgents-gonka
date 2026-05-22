<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight text-white">Account Management</h2>
        <p class="mt-1 text-sm text-gonka-muted">
          Create and manage operator accounts. Usernames are email addresses.
        </p>
      </div>
      <button class="btn" :disabled="loading" @click="loadUsers">
        {{ loading ? "Refreshing…" : "Refresh" }}
      </button>
    </header>

    <!-- One-time credential reveal -->
    <section
      v-if="freshCredential"
      class="card border-emerald-500/40 bg-emerald-500/5 p-5"
    >
      <div class="flex items-start justify-between gap-4">
        <div>
          <h3 class="text-sm font-semibold text-emerald-200">
            New password for {{ freshCredential.username }}
          </h3>
          <p class="mt-1 text-xs text-emerald-100/70">
            Shown only once — copy it now and hand it to the user over a secure channel.
          </p>
        </div>
        <button class="btn btn-ghost" type="button" @click="freshCredential = null">
          Dismiss
        </button>
      </div>
      <div class="mt-3 flex items-center gap-2">
        <code
          class="flex-1 rounded-lg border border-emerald-500/30 bg-[#05080F] px-3 py-2 font-mono text-sm text-emerald-200"
        >{{ freshCredential.password }}</code>
        <button class="btn" type="button" @click="copyPassword">
          {{ copied ? "Copied" : "Copy" }}
        </button>
      </div>
    </section>

    <!-- Create user -->
    <section class="card space-y-4 p-5">
      <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
        Create user
      </h3>
      <form class="flex flex-wrap items-end gap-3" @submit.prevent="createUser">
        <div class="min-w-[16rem] flex-1">
          <label class="label mb-1" for="newEmail">Email address</label>
          <input
            id="newEmail"
            v-model="newEmail"
            type="email"
            class="input"
            placeholder="analyst@example.com"
            :disabled="creating"
          />
        </div>
        <button
          class="btn btn-primary"
          type="submit"
          :disabled="creating || !newEmail.trim()"
        >
          {{ creating ? "Creating…" : "Create user" }}
        </button>
      </form>
      <p v-if="createError" class="text-xs text-red-300">{{ createError }}</p>
    </section>

    <!-- User list -->
    <section class="card">
      <div class="border-b border-gonka-border px-5 py-3">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Accounts · {{ users.length }}
        </h3>
      </div>

      <EmptyState
        v-if="!users.length && !loading"
        title="No accounts"
        description="Create the first operator account above."
      />

      <div v-else class="divide-y divide-gonka-border">
        <article
          v-for="u in users"
          :key="u.username"
          class="grid grid-cols-1 items-center gap-3 px-5 py-3 sm:grid-cols-12"
        >
          <div class="sm:col-span-5">
            <div class="flex items-center gap-2">
              <span class="font-mono text-sm text-white">{{ u.username }}</span>
              <span
                v-if="u.builtin"
                class="chip text-[10px] uppercase tracking-wide"
              >built-in</span>
            </div>
            <div class="text-xs text-gonka-muted">
              {{ u.created_at ? `created ${formatTs(u.created_at)}` : "rotates on restart" }}
            </div>
          </div>
          <div class="sm:col-span-3">
            <span
              class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ring-1 ring-inset"
              :class="
                u.role === 'admin'
                  ? 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30'
                  : 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30'
              "
            >
              {{ u.role === "admin" ? "Super admin" : "User" }}
            </span>
          </div>
          <div class="flex gap-2 sm:col-span-4 sm:justify-end">
            <template v-if="!u.builtin">
              <button
                class="btn btn-ghost"
                type="button"
                :disabled="busyUser === u.username"
                @click="resetPassword(u.username)"
              >
                Reset password
              </button>
              <button
                class="btn btn-danger"
                type="button"
                :disabled="busyUser === u.username"
                @click="deleteUser(u.username)"
              >
                Delete
              </button>
            </template>
            <span v-else class="text-xs text-gonka-muted">No actions</span>
          </div>
        </article>
      </div>
    </section>

    <p v-if="actionError" class="text-xs text-red-300">{{ actionError }}</p>
  </div>
</template>

<script setup lang="ts">
import type { NewCredential, UserRecord } from "~/composables/useApi";

const api = useApi();

const users = ref<UserRecord[]>([]);
const loading = ref(false);
const newEmail = ref("");
const creating = ref(false);
const createError = ref("");
const actionError = ref("");
const busyUser = ref<string | null>(null);
const freshCredential = ref<NewCredential | null>(null);
const copied = ref(false);

function errorMessage(e: unknown): string {
  const detail = (e as { data?: { detail?: string } })?.data?.detail;
  if (detail) return detail;
  return e instanceof Error ? e.message : String(e);
}

function formatTs(iso: string): string {
  return iso.replace("T", " ").slice(0, 19);
}

async function loadUsers() {
  loading.value = true;
  actionError.value = "";
  try {
    users.value = await api.listUsers();
  } catch (e) {
    actionError.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}

async function createUser() {
  createError.value = "";
  creating.value = true;
  try {
    const cred = await api.createUser(newEmail.value.trim());
    freshCredential.value = cred;
    copied.value = false;
    newEmail.value = "";
    await loadUsers();
  } catch (e) {
    createError.value = errorMessage(e);
  } finally {
    creating.value = false;
  }
}

async function resetPassword(username: string) {
  actionError.value = "";
  busyUser.value = username;
  try {
    const cred = await api.resetUserPassword(username);
    freshCredential.value = cred;
    copied.value = false;
  } catch (e) {
    actionError.value = errorMessage(e);
  } finally {
    busyUser.value = null;
  }
}

async function deleteUser(username: string) {
  if (!confirm(`Delete account ${username}? This cannot be undone.`)) return;
  actionError.value = "";
  busyUser.value = username;
  try {
    await api.deleteUser(username);
    if (freshCredential.value?.username === username) freshCredential.value = null;
    await loadUsers();
  } catch (e) {
    actionError.value = errorMessage(e);
  } finally {
    busyUser.value = null;
  }
}

async function copyPassword() {
  if (!freshCredential.value) return;
  try {
    await navigator.clipboard.writeText(freshCredential.value.password);
    copied.value = true;
    setTimeout(() => (copied.value = false), 2000);
  } catch {
    /* clipboard blocked — user can still select the text manually */
  }
}

onMounted(loadUsers);
</script>
