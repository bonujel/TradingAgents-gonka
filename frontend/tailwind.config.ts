import type { Config } from "tailwindcss";

export default <Partial<Config>>{
  content: [
    "./components/**/*.{vue,js,ts}",
    "./layouts/**/*.vue",
    "./pages/**/*.vue",
    "./composables/**/*.{js,ts}",
    "./plugins/**/*.{js,ts}",
    "./app.vue",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        gonka: {
          bg: "#06090F",
          surface: "#0E131C",
          card: "#121826",
          border: "#1E2434",
          borderStrong: "#2A3245",
          muted: "#7A869A",
          text: "#E6EAF2",
          accent: "#22C55E",
          accentDim: "#15803D",
          danger: "#EF4444",
          warning: "#F59E0B",
          info: "#3B82F6",
        },
      },
      boxShadow: {
        card: "0 1px 0 0 rgba(255,255,255,0.02) inset, 0 8px 32px -16px rgba(0,0,0,0.6)",
      },
    },
  },
  plugins: [],
};
