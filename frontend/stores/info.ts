import { defineStore } from "pinia";
import type { InfoResponse } from "~/composables/useApi";

export const useInfoStore = defineStore("info", {
  state: () => ({
    info: null as InfoResponse | null,
    loaded: false,
    error: null as string | null,
  }),

  actions: {
    async refresh() {
      const api = useApi();
      try {
        this.info = await api.info();
        this.error = null;
      } catch (err: unknown) {
        this.error = err instanceof Error ? err.message : String(err);
      } finally {
        this.loaded = true;
      }
    },
  },
});
