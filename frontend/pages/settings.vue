<template>
  <div class="space-y-6">
    <header>
      <h2 class="text-2xl font-semibold tracking-tight text-white">Settings</h2>
      <p class="mt-1 text-sm text-gonka-muted">
        Connection mode, credentials, and model selection. Stored at
        <code class="font-mono text-emerald-300">~/.tradingagents/app/settings.json</code>.
      </p>
    </header>

    <section v-if="loading" class="card p-8 text-center text-gonka-muted">Loading settings…</section>

    <template v-else>
      <section class="card p-5">
        <h3 class="mb-4 text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Connection mode
        </h3>
        <div class="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            class="flex items-start gap-3 rounded-xl border p-4 text-left transition"
            :class="
              draft.mode === 'router'
                ? 'border-emerald-500/60 bg-emerald-500/5'
                : 'border-gonka-border bg-gonka-surface hover:border-gonka-borderStrong'
            "
            @click="draft.mode = 'router'"
          >
            <span
              class="mt-0.5 grid h-5 w-5 place-items-center rounded-full border"
              :class="
                draft.mode === 'router'
                  ? 'border-emerald-500 bg-emerald-500'
                  : 'border-gonka-border'
              "
            >
              <span v-if="draft.mode === 'router'" class="h-2 w-2 rounded-full bg-white"></span>
            </span>
            <div>
              <div class="font-semibold text-gonka-text">Router</div>
              <p class="mt-1 text-xs text-gonka-muted">
                Bearer token via <span class="font-mono">api.gonkascan.com</span>. Simplest setup
                — no on-chain account required.
              </p>
            </div>
          </button>

          <button
            type="button"
            class="flex items-start gap-3 rounded-xl border p-4 text-left transition"
            :class="
              draft.mode === 'sdk'
                ? 'border-emerald-500/60 bg-emerald-500/5'
                : 'border-gonka-border bg-gonka-surface hover:border-gonka-borderStrong'
            "
            @click="draft.mode = 'sdk'"
          >
            <span
              class="mt-0.5 grid h-5 w-5 place-items-center rounded-full border"
              :class="
                draft.mode === 'sdk'
                  ? 'border-emerald-500 bg-emerald-500'
                  : 'border-gonka-border'
              "
            >
              <span v-if="draft.mode === 'sdk'" class="h-2 w-2 rounded-full bg-white"></span>
            </span>
            <div>
              <div class="font-semibold text-gonka-text">SDK direct</div>
              <p class="mt-1 text-xs text-gonka-muted">
                ECDSA signing via <span class="font-mono">gonka-openai</span>. Pays from your
                on-chain GNK balance; gateway address auto-discovered via
                <span class="font-mono">/v1/identity</span>.
              </p>
            </div>
          </button>
        </div>
      </section>

      <section v-if="draft.mode === 'router'" class="card space-y-3 p-5">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Router credentials
        </h3>
        <div>
          <label class="label mb-1" for="router_api_key">GONKA_API_KEY</label>
          <input
            id="router_api_key"
            v-model="draft.router_api_key"
            type="password"
            class="input font-mono"
            :placeholder="storedSettings?.router_api_key_set ? storedPreview('router') : 'sk-…'"
            autocomplete="off"
          />
          <p class="mt-1 text-xs text-gonka-muted">
            Bearer token issued by the
            <a
              class="text-emerald-300 underline-offset-2 hover:underline"
              href="https://router.gonkascan.com/dashboard"
              target="_blank"
              rel="noopener"
            >router.gonkascan.com dashboard</a
            >. Leave blank to keep the stored value.
          </p>
        </div>
      </section>

      <section v-else class="card space-y-3 p-5">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          SDK credentials
        </h3>
        <div>
          <label class="label mb-1" for="sdk_private_key">GONKA_PRIVATE_KEY</label>
          <input
            id="sdk_private_key"
            v-model="draft.sdk_private_key"
            type="password"
            class="input font-mono"
            :placeholder="storedSettings?.sdk_private_key_set ? storedPreview('sdk') : '0x…'"
            autocomplete="off"
          />
          <p class="mt-1 text-xs text-gonka-muted">
            secp256k1 hex private key (with or without <code>0x</code>). Used to sign every
            request; the gateway's whitelisted address is auto-discovered via
            <span class="font-mono">/v1/identity</span> and used as the on-chain transfer agent.
          </p>
        </div>
        <div>
          <label class="label mb-1" for="sdk_source_url">GONKA_SOURCE_URL</label>
          <input
            id="sdk_source_url"
            v-model="draft.sdk_source_url"
            type="text"
            class="input font-mono"
            placeholder="https://node4.gonka.ai"
          />
          <p class="mt-1 text-xs text-gonka-muted">
            Public inference gateway URL. The default works on Gonka mainnet.
          </p>
        </div>
      </section>

      <section class="card space-y-4 p-5">
        <h3 class="text-sm font-semibold uppercase tracking-wider text-gonka-muted">
          Model & runner
        </h3>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label mb-1">Deep-think model</label>
            <select v-model="draft.deep_model" class="input">
              <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
            </select>
            <p class="mt-1 text-xs text-gonka-muted">
              Used by the analysts, research manager, trader, and portfolio manager.
            </p>
          </div>
          <div>
            <label class="label mb-1">Quick-think model</label>
            <select v-model="draft.quick_model" class="input">
              <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
            </select>
            <p class="mt-1 text-xs text-gonka-muted">
              Used for lightweight intermediate calls; safe to leave equal to deep.
            </p>
          </div>
        </div>
        <div class="max-w-xs">
          <label class="label mb-1">Manual default workers</label>
          <input v-model.number="draft.max_workers" type="number" min="1" max="16" class="input" />
          <p class="mt-1 text-xs text-gonka-muted">
            Default pre-fill for the Tasks page launcher. Scheduled runs have an
            independent worker count on the
            <NuxtLink to="/schedule" class="text-emerald-300 hover:underline">Schedule</NuxtLink>
            page.
          </p>
        </div>
      </section>

      <section class="flex items-center gap-3">
        <button
          class="btn btn-primary"
          type="button"
          :disabled="saving"
          @click="save"
        >
          {{ saving ? "Saving…" : "Save settings" }}
        </button>
        <span v-if="message" class="text-xs text-emerald-300">{{ message }}</span>
        <span v-if="error" class="text-xs text-red-300">{{ error }}</span>
        <span class="ml-auto text-xs text-gonka-muted">
          {{ storedSettings?.configured ? "Current credentials look complete." : "Credentials incomplete." }}
        </span>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { SettingsResponse, SettingsUpdate } from "~/composables/useApi";

const api = useApi();
const infoStore = useInfoStore();

const loading = ref(true);
const saving = ref(false);
const message = ref("");
const error = ref("");

const storedSettings = ref<SettingsResponse | null>(null);

const draft = reactive<SettingsUpdate>({
  mode: "router",
  router_api_key: "",
  sdk_private_key: "",
  sdk_source_url: "https://node4.gonka.ai",
  deep_model: "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
  quick_model: "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
  max_workers: 4,
});

const models = computed(() =>
  infoStore.info?.models?.length
    ? infoStore.info.models
    : ["Qwen/Qwen3-235B-A22B-Instruct-2507-FP8", "moonshotai/Kimi-K2.6"]
);

function storedPreview(prefix: "router" | "sdk"): string {
  const s = storedSettings.value;
  if (!s) return "";
  if (prefix === "router") return s.router_api_key_preview;
  return s.sdk_private_key_preview;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [settings] = await Promise.all([
      api.getSettings(),
      infoStore.refresh(),
    ]);
    storedSettings.value = settings;
    draft.mode = settings.mode;
    draft.sdk_source_url = settings.sdk_source_url || "https://node4.gonka.ai";
    draft.deep_model = settings.deep_model;
    draft.quick_model = settings.quick_model;
    draft.max_workers = settings.max_workers;
    draft.router_api_key = "";
    draft.sdk_private_key = "";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    const payload: SettingsUpdate = {
      mode: draft.mode,
      sdk_source_url: draft.sdk_source_url,
      deep_model: draft.deep_model,
      quick_model: draft.quick_model,
      max_workers: draft.max_workers,
    };
    if (draft.router_api_key) payload.router_api_key = draft.router_api_key;
    if (draft.sdk_private_key) payload.sdk_private_key = draft.sdk_private_key;
    storedSettings.value = await api.putSettings(payload);
    message.value = "Saved.";
    draft.router_api_key = "";
    draft.sdk_private_key = "";
    await infoStore.refresh();
  } catch (e: unknown) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}

function errorMessage(e: unknown): string {
  if (typeof e === "object" && e !== null && "data" in e) {
    const data = (e as { data?: { detail?: string } }).data;
    if (data?.detail) return data.detail;
  }
  return e instanceof Error ? e.message : String(e);
}

onMounted(load);
</script>
