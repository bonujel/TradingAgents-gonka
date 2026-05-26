#!/usr/bin/env bash
#
# start.sh — (re)launch the TradingAgents · Gonka operator console.
#
#   ./start.sh         stop everything, then start backend + frontend
#   ./start.sh stop    stop everything and exit
#
# Backend  : uvicorn app.api:app                            -> http://127.0.0.1:8000  (log: log-back.log)
# Frontend : nuxt build  +  node .output/server/index.mjs   -> http://127.0.0.1:3000  (log: log-front.log)
#
# Frontend runs the production Nitro server (not `nuxt dev`) so it can serve
# real hostnames behind a reverse proxy — `nuxt dev` is Vite-based and rejects
# requests whose Host header is not in `server.allowedHosts`, which 400s every
# request from a public domain.
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

# Pidfiles live under $REPO_DIR/.run so each repo clone tracks its own
# processes (and stop_all from this clone never touches another clone's).
# Old layouts used unscoped ``pkill -f "uvicorn app.api"``-style patterns
# which would happily kill an unrelated uvicorn / Nuxt service running on
# the same box.
RUN_DIR="$REPO_DIR/.run"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"

# ─── Helpers ────────────────────────────────────────────────────────────────
log() { printf '\033[0;36m[start.sh]\033[0m %s\n' "$*"; }

# Kill ``pid`` only when it is still alive AND its /proc/<pid>/cmdline
# contains ``sentinel``. The sentinel guards against PID reuse: between our
# launch and our stop the kernel may have recycled the pid into an
# unrelated process — without the cmdline check we would happily ``kill``
# that bystander.
kill_if_owns() {
  local pid="$1" sentinel="$2"
  [ -n "$pid" ] || return 0
  [ -e "/proc/$pid" ] || return 0
  if grep -q -- "$sentinel" "/proc/$pid/cmdline" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
}

stop_all() {
  log "Stopping backend, frontend, and analysis subprocesses..."

  # Backend + frontend: kill by recorded PID, after verifying the cmdline.
  if [ -f "$BACKEND_PID_FILE" ]; then
    kill_if_owns "$(cat "$BACKEND_PID_FILE")" "uvicorn"
    rm -f "$BACKEND_PID_FILE"
  fi
  if [ -f "$FRONTEND_PID_FILE" ]; then
    kill_if_owns "$(cat "$FRONTEND_PID_FILE")" "index.mjs"
    rm -f "$FRONTEND_PID_FILE"
  fi

  # app.runner subprocesses are spawned detached by the backend, so we
  # don't own their PIDs at restart time. Scope by /proc/<pid>/cwd: only
  # kill app.runner processes whose working directory IS this clone, so a
  # second clone of TradingAgents on the same box keeps running.
  if pgrep -f "app\.runner" >/dev/null 2>&1; then
    for pid in $(pgrep -f "app\.runner" 2>/dev/null); do
      if [ -e "/proc/$pid/cwd" ]; then
        local cwd
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
        if [ "$cwd" = "$REPO_DIR" ]; then
          kill "$pid" 2>/dev/null || true
        fi
      fi
    done
  fi

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

mkdir -p "$RUN_DIR"

log "Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT} ..."
cd "$REPO_DIR"
nohup uvicorn app.api:app \
  --host "$BACKEND_HOST" --port "$BACKEND_PORT" --log-level info \
  > "$BACK_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$BACKEND_PID_FILE"
log "Backend PID $BACKEND_PID — log: $BACK_LOG"

log "Building frontend (nuxt build → .output/) ..."
cd "$REPO_DIR/frontend"
# Build is foreground + blocking on purpose: a failed build must abort the
# whole restart instead of leaving a stale .output running. `set -e` at the
# top of the script means a non-zero npm exit kills us here.
: > "$FRONT_LOG"  # truncate so the build output is the first thing in the log
if ! npm run build >> "$FRONT_LOG" 2>&1; then
  log "ERROR: frontend build failed — see $FRONT_LOG. Aborting restart."
  log "Backend (PID $BACKEND_PID) is up but the frontend is NOT started."
  exit 1
fi
log "Build OK."

log "Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT} (production Nitro server) ..."
# .output/server/index.mjs is a Nitro server. It does NOT take --host/--port
# CLI flags; configuration is via NITRO_HOST / NITRO_PORT env vars.
NITRO_HOST="$FRONTEND_HOST" NITRO_PORT="$FRONTEND_PORT" \
  nohup node .output/server/index.mjs >> "$FRONT_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"
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
