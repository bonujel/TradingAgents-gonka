import { defineStore } from "pinia";

/**
 * Cached ``ticker → company name`` map for the S&P 500 universe.
 *
 * The backend serves the full ~500-entry mapping from
 * ``GET /api/tickers/names`` (open to any authenticated user — the
 * Decisions page renders names for normal users too). We pull it once
 * per session via :func:`ensureLoaded` and resolve names locally from
 * then on; a missing ticker falls back to the ticker itself so callers
 * never have to null-check.
 */
export const useTickerNamesStore = defineStore("tickerNames", {
  state: () => ({
    names: {} as Record<string, string>,
    loaded: false,
    loading: false,
    error: null as string | null,
  }),

  getters: {
    // Returning a function (rather than a computed getter) lets components
    // call ``store.lookup(ticker)`` reactively without recomputing the
    // entire dictionary on every component update.
    lookup:
      (state) =>
      (ticker: string): string => {
        if (!ticker) return "";
        return state.names[ticker] || ticker;
      },
  },

  actions: {
    async ensureLoaded() {
      if (this.loaded || this.loading) return;
      this.loading = true;
      try {
        const api = useApi();
        this.names = await api.tickerNames();
        this.error = null;
        this.loaded = true;
      } catch (err: unknown) {
        this.error = err instanceof Error ? err.message : String(err);
      } finally {
        this.loading = false;
      }
    },
  },
});
