# TradingAgents · Gonka — Nuxt Frontend

A Nuxt 3 + Tailwind CSS operator console for the TradingAgents daily
pipeline. Talks to the FastAPI backend at `app.api:app`.

## Tech stack

- Nuxt 3 (Vue 3 + Vite + TypeScript)
- Tailwind CSS via `@nuxtjs/tailwindcss`
- Pinia for shared state (`stores/info.ts`)
- `marked` for rendering analyst-report markdown

## Pages

- `/` — Decisions browser (filter by date / ticker, paginated cards).
- `/tasks` — Launch runs, watch active workers, view recent run history.
- `/settings` — Switch Router / SDK mode, store credentials, pick models.

## Local development

1. Install Node.js 20+ (`brew install node` on macOS or use `nvm`).
2. Install dependencies:

   ```bash
   cd frontend
   npm install
   ```

3. Start the FastAPI backend in another shell from the repo root:

   ```bash
   uvicorn app.api:app --reload --port 8000
   ```

4. Start the Nuxt dev server:

   ```bash
   npm run dev
   ```

   Open <http://127.0.0.1:3000>.

## Configuration

- `NUXT_PUBLIC_API_BASE` overrides the API host (defaults to
  `http://127.0.0.1:8000`). Set this when serving the SPA from a different
  origin than the backend.
- Tailwind tokens live in [`tailwind.config.ts`](./tailwind.config.ts);
  the `gonka.*` palette mirrors the dark gonkascan dashboard.

## Production build

```bash
npm run build
node .output/server/index.mjs
```

Set `NUXT_PUBLIC_API_BASE` to your public backend URL before building if
the frontend ships to a different host.
