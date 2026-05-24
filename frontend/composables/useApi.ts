/**
 * Tiny wrapper around `$fetch` that prefixes API base URL from runtime
 * config and surfaces backend `{detail: "..."}` errors as native messages.
 */
import type { FetchOptions } from "ofetch";

export interface DecisionRow {
  id: number;
  ticker: string;
  trade_date: string;
  rating: string | null;
  final_decision: string | null;
  market_report: string | null;
  sentiment_report: string | null;
  news_report: string | null;
  fundamentals_report: string | null;
  investment_plan: string | null;
  trader_plan: string | null;
  model_provider: string | null;
  deep_model: string | null;
  quick_model: string | null;
  error: string | null;
  created_at: string;
}

export interface ActiveTask {
  pid: number;
  kind: "manual" | "scheduled";
  started_at: string;
  tickers: string[];
  workers: number;
  log_path: string;
  mode: string;
  deep_model: string | null;
  elapsed_seconds: number;
}

export interface CapacityState {
  current: number;
  max: number;
}

export interface CapacityMap {
  manual: CapacityState;
  scheduled: CapacityState;
}

export interface RunRecord {
  id: number;
  run_date: string;
  started_at: string;
  finished_at: string | null;
  tickers: string[];
  success_count: number;
  failure_count: number;
  notes: string | null;
  elapsed_seconds: number | null;
}

export interface InfoResponse {
  mode: "router" | "sdk";
  deep_model: string | null;
  quick_model: string | null;
  max_workers: number;
  configured: boolean;
  models: string[];
  db_path: string;
  capacity: CapacityMap;
}

export interface ScheduleConfig {
  enabled: boolean;
  start_hour: number;
  start_minute: number;
  interval_hours: number;
  workers: number;
  tickers: string[];
  pending_catch_up?: boolean;
  missed_count?: number;
  last_missed_at?: string | null;
  last_catch_up_at?: string | null;
  last_regular_fire_at?: string | null;
  last_start_error?: string | null;
  pause_clears_pending?: boolean;
  clear_pending?: boolean;
}

export interface ScheduleResponse {
  config: ScheduleConfig;
  next_run: string | null;
  scheduler_running: boolean;
  server_time: {
    now: string;
    tz_name: string;
    tz_offset_seconds: number;
  };
  capacity: CapacityMap;
}

export interface SettingsResponse {
  mode: "router" | "sdk";
  router_api_key_set: boolean;
  router_api_key_preview: string;
  sdk_private_key_set: boolean;
  sdk_private_key_preview: string;
  sdk_source_url: string;
  deep_model: string;
  quick_model: string;
  max_workers: number;
  qwen_max_tokens: number;
  kimi_max_tokens: number;
  llm_debug: boolean;
  configured: boolean;
}

export interface SettingsUpdate {
  mode: "router" | "sdk";
  router_api_key?: string;
  sdk_private_key?: string;
  sdk_source_url?: string;
  deep_model?: string;
  quick_model?: string;
  max_workers: number;
  qwen_max_tokens: number;
  kimi_max_tokens: number;
  llm_debug: boolean;
}

export interface LoginResponse {
  token: string;
  username: string;
  role: "admin" | "user";
}

export interface UserRecord {
  username: string;
  role: "admin" | "user";
  created_at: string | null;
  builtin: boolean;
}

export interface NewCredential {
  username: string;
  password: string;
}

function buildUrl(base: string, path: string): string {
  if (path.startsWith("http")) return path;
  return `${base.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

export function useApi() {
  const config = useRuntimeConfig();
  const base = config.public.apiBase as string;

  async function request<T>(path: string, opts: FetchOptions<"json"> = {}): Promise<T> {
    const auth = useAuthStore();
    const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
    try {
      return await $fetch<T>(buildUrl(base, path), { ...opts, headers });
    } catch (err: unknown) {
      // A 401 means the token is missing/expired — drop the session and
      // send the operator back to the login screen. Skip the redirect for
      // the login call itself so a bad password shows inline instead.
      const e = err as { response?: { status?: number }; statusCode?: number };
      const status = e?.response?.status ?? e?.statusCode;
      if (status === 401 && path !== "/api/auth/login") {
        auth.clear();
        if (typeof window !== "undefined" && window.location.pathname !== "/login") {
          navigateTo("/login");
        }
      }
      throw err;
    }
  }

  return {
    login: (username: string, password: string) =>
      request<LoginResponse>("/api/auth/login", {
        method: "POST",
        body: { username, password },
      }),
    me: () => request<LoginResponse>("/api/auth/me"),
    changePassword: (oldPassword: string, newPassword: string) =>
      request<{ ok: boolean }>("/api/auth/change-password", {
        method: "POST",
        body: { old_password: oldPassword, new_password: newPassword },
      }),
    listUsers: () => request<UserRecord[]>("/api/users"),
    createUser: (email: string) =>
      request<NewCredential>("/api/users", { method: "POST", body: { email } }),
    resetUserPassword: (username: string) =>
      request<NewCredential>(
        `/api/users/${encodeURIComponent(username)}/reset-password`,
        { method: "POST" }
      ),
    deleteUser: (username: string) =>
      request<{ username: string; deleted: boolean }>(
        `/api/users/${encodeURIComponent(username)}`,
        { method: "DELETE" }
      ),
    info: () => request<InfoResponse>("/api/info"),
    getSettings: () => request<SettingsResponse>("/api/settings"),
    putSettings: (body: SettingsUpdate) =>
      request<SettingsResponse>("/api/settings", { method: "PUT", body }),
    listDates: () => request<string[]>("/api/decisions/dates"),
    listDecisions: (params: { date?: string; ticker?: string; limit?: number }) =>
      request<DecisionRow[]>("/api/decisions", { params }),
    getDecision: (ticker: string, tradeDate: string) =>
      request<DecisionRow>(`/api/decisions/${encodeURIComponent(ticker)}/${tradeDate}`),
    listActiveRuns: () => request<ActiveTask[]>("/api/runs/active"),
    listRecentRuns: (limit = 10) =>
      request<RunRecord[]>("/api/runs/recent", { params: { limit } }),
    startRun: (body: { tickers: string[]; workers: number; kind?: "manual" | "scheduled" }) =>
      request<ActiveTask>("/api/runs", { method: "POST", body }),
    getSchedule: () => request<ScheduleResponse>("/api/schedule"),
    putSchedule: (body: ScheduleConfig) =>
      request<ScheduleResponse>("/api/schedule", { method: "PUT", body }),
    stopRun: (pid: number) =>
      request<{ pid: number; stopped: boolean }>(`/api/runs/${pid}`, { method: "DELETE" }),
    readLog: (pid: number, lines = 30) =>
      request<{ pid: number; log: string }>(`/api/runs/${pid}/log`, {
        params: { lines },
      }),
    topTickers: (n = 20) => request<string[]>("/api/tickers/top", { params: { n } }),
    sp100Tickers: () => request<string[]>("/api/tickers/sp100"),
    sp500Tickers: () => request<string[]>("/api/tickers/sp500"),
  };
}
