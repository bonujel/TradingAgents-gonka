import { defineStore } from "pinia";

const TOKEN_KEY = "ta_token";
const USERNAME_KEY = "ta_username";
const ROLE_KEY = "ta_role";

/**
 * Session store. Backed by localStorage so a page reload keeps the user
 * logged in. SPA mode (ssr: false) guarantees localStorage is available
 * wherever this runs.
 */
export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: null as string | null,
    username: null as string | null,
    role: null as string | null,
    hydrated: false,
  }),

  getters: {
    isAuthenticated: (s): boolean => !!s.token,
    isAdmin: (s): boolean => s.role === "admin",
  },

  actions: {
    /** Pull any persisted session out of localStorage (idempotent). */
    hydrate() {
      if (this.hydrated) return;
      if (typeof localStorage !== "undefined") {
        this.token = localStorage.getItem(TOKEN_KEY);
        this.username = localStorage.getItem(USERNAME_KEY);
        this.role = localStorage.getItem(ROLE_KEY);
      }
      this.hydrated = true;
    },

    setSession(token: string, username: string, role: string) {
      this.token = token;
      this.username = username;
      this.role = role;
      this.hydrated = true;
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(USERNAME_KEY, username);
        localStorage.setItem(ROLE_KEY, role);
      }
    },

    clear() {
      this.token = null;
      this.username = null;
      this.role = null;
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USERNAME_KEY);
        localStorage.removeItem(ROLE_KEY);
      }
    },
  },
});
