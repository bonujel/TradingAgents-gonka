// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-05-15",
  devtools: { enabled: false },

  modules: ["@nuxtjs/tailwindcss", "@pinia/nuxt"],

  css: ["~/assets/css/main.css"],

  app: {
    head: {
      title: "TradingAgents · Gonka",
      meta: [
        { charset: "utf-8" },
        { name: "viewport", content: "width=device-width, initial-scale=1" },
        {
          name: "description",
          content:
            "Operator console for the TradingAgents daily S&P 500 pipeline running on Gonka inference.",
        },
      ],
      link: [
        { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
        {
          rel: "preconnect",
          href: "https://fonts.googleapis.com",
        },
        {
          rel: "preconnect",
          href: "https://fonts.gstatic.com",
          crossorigin: "",
        },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap",
        },
      ],
    },
  },

  runtimeConfig: {
    public: {
      // Default to an empty string so client-side $fetch issues *relative*
      // URLs (/api/...). The browser then hits the same origin as the page,
      // and the Nitro server itself (see ``nitro.routeRules`` below) proxies
      // those paths to the FastAPI backend on 127.0.0.1:8000.
      //
      // Why not the previous default (``http://127.0.0.1:8000``)? In
      // production the runtimeConfig.public.apiBase value is baked into the
      // client bundle, so every browser literally tries to reach
      // ``http://127.0.0.1:8000`` — its OWN localhost, where nothing is
      // listening. ``Failed to fetch`` on every API call.
      //
      // Set NUXT_PUBLIC_API_BASE at build time only if the backend lives on
      // a different origin (separate api.* domain etc.) — for the single-host
      // reverse-proxy deployment, leave it unset.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || "",
    },
  },

  typescript: { strict: true, shim: false },

  nitro: {
    // routeRules works in both dev and production — replaces the old
    // ``devProxy`` block which only fired under ``nuxt dev``. Once we
    // switched to ``node .output/server/index.mjs`` for production, the
    // /api proxy stopped working and every browser request 400/404'd
    // (some hit the Nitro server as missing pages, some hit
    // ``http://127.0.0.1:8000`` baked into the client bundle).
    routeRules: {
      "/api/**": { proxy: "http://127.0.0.1:8000/api/**" },
    },
  },
});
