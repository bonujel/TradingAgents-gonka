import { defineStore } from "pinia";

/**
 * Global controller for the in-place login modal. Any component can
 * call `show(returnTo)` — the modal mounts in `app.vue` and is rendered
 * over the whole UI via Teleport.
 */
export const useLoginModalStore = defineStore("loginModal", {
  state: () => ({
    open: false,
    returnTo: null as string | null,
  }),
  actions: {
    show(returnTo: string | null = null) {
      this.open = true;
      this.returnTo = returnTo;
    },
    hide() {
      this.open = false;
      this.returnTo = null;
    },
  },
});
