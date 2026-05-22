#!/usr/bin/env bash
#
# start.sh — (re)launch the TradingAgents · Gonka operator console.
#
#   ./start.sh         stop everything, then start backend + frontend
#   ./start.sh stop    stop everything and exit
#
# Backend  : uvicorn app.api:app   -> http://127.0.0.1:8000  (log: log-back.log)
# Frontend : nuxt dev              -> http://127.0.0.1:3000  (log: log-front.log)
#
# "Stop everything" also kills detached `python -m app.runner` analysis
# subprocesses spawned by the backend, so a restart starts from a clean slate.

set -euo pipefail

# ─── Config ─────────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV="${CONDA_ENV:-tradingagents}"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

BACK_LOG="$REPO_DIR/log-back.log"
FRONT_LOG="$REPO_DIR/log-front.log"

# ─── Helpers ────────────────────────────────────────────────────────────────
log() { printf '\033[0;36m[start.sh]\033[0m %s\n' "$*"; }

stop_all() {
  log "Stopping backend, frontend, and analysis subprocesses..."
  # `|| true` — pkill exits non-zero when nothing matched, which is fine.
  pkill -f "uvicorn app.api"  2>/dev/null || true   # backend
  pkill -f "app\.runner"      2>/dev/null || true   # detached analysis runs
  pkill -f "nuxt dev"         2>/dev/null || true   # frontend dev server
  pkill -f "npm run dev"      2>/dev/null || true   # frontend npm wrapper
  # Give the OS a moment to release the listening ports.
  sleep 2
  # Drop the stale active-task index so the next backend boot starts clean.
  rm -f "$HOME/.tradingagents/app/active_tasks.json" 2>/dev/null || true
  log "Stopped."
}

activate_conda() {
  # Locate conda.sh from $CONDA_EXE if set, else the usual install dirs.
  local conda_sh=""
  if [ -n "${CONDA_EXE:-}" ]; then
    conda_sh="$(dirname "$(dirname "$CONDA_EXE")")/etc/profile.d/conda.sh"
  fi
  if [ -z "$conda_sh" ] || [ ! -f "$conda_sh" ]; then
    for base in "$HOME/miniconda3" "$HOME/anaconda3" /opt/conda; do
      if [ -f "$base/etc/profile.d/conda.sh" ]; then
        conda_sh="$base/etc/profile.d/conda.sh"
        break
      fi
    done
  fi
  if [ -z "$conda_sh" ] || [ ! -f "$conda_sh" ]; then
    log "ERROR: could not find conda. Set CONDA_EXE or install to ~/miniconda3."
    exit 1
  fi
  # shellcheck disable=SC1090
  source "$conda_sh"
  conda activate "$CONDA_ENV"
}

wait_for_http() {
  # wait_for_http <url> <label> <max_seconds>
  local url="$1" label="$2" max="$3" i
  for ((i = 1; i <= max; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  log "WARNING: $label did not respond within ${max}s — check its log."
  return 1
}

# ─── Stop-only mode ─────────────────────────────────────────────────────────
if [ "${1:-}" = "stop" ]; then
  stop_all
  exit 0
fi

# ─── Restart ────────────────────────────────────────────────────────────────
stop_all
activate_conda

log "Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT} ..."
cd "$REPO_DIR"
nohup uvicorn app.api:app \
  --host "$BACKEND_HOST" --port "$BACKEND_PORT" --log-level info \
  > "$BACK_LOG" 2>&1 &
BACKEND_PID=$!
log "Backend PID $BACKEND_PID — log: $BACK_LOG"

log "Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT} ..."
cd "$REPO_DIR/frontend"
nohup npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
  > "$FRONT_LOG" 2>&1 &
FRONTEND_PID=$!
log "Frontend PID $FRONTEND_PID — log: $FRONT_LOG"

# ─── Health checks ──────────────────────────────────────────────────────────
cd "$REPO_DIR"
wait_for_http "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" "Backend" 30 || true
wait_for_http "http://${FRONTEND_HOST}:${FRONTEND_PORT}/login" "Frontend" 40 || true

echo
log "Up. Open  http://${FRONTEND_HOST}:${FRONTEND_PORT}"
# Surface the super-admin password — regenerated on every backend start.
if grep -q "Super-admin account" "$BACK_LOG" 2>/dev/null; then
  echo "----------------------------------------------------------------"
  grep -A4 "Super-admin account" "$BACK_LOG" | sed 's/^/  /'
  echo "----------------------------------------------------------------"
fi
log "Tail logs:  tail -f log-back.log   /   tail -f log-front.log"
log "Stop all :  ./start.sh stop"
