#!/usr/bin/env bash
# Background poller — snapshots scripts/analyze_kimi_run.py output every 6 minutes
# until the Kimi runner exits. Snapshots accumulate in
# dev_notes/debug_kimi_no_rating_output/snapshots/ so the final-synthesis
# wakeup can show the evolution of the run.
set -euo pipefail

PYBIN="/home/szy/miniconda3/envs/tradingagents/bin/python"
ROOT="/home/szy/TradingAgents-gonka"
SNAP_DIR="$ROOT/dev_notes/debug_kimi_no_rating_output/snapshots"
mkdir -p "$SNAP_DIR"

# Anchor at the time the runner started. Anything before this in
# llm_debug.jsonl is from a prior run (Qwen smoke etc.).
SINCE_TS="${1:-2026-05-23T15:23}"

# Watch this PID; exit when it's gone.
RUNNER_PID="${2:-220318}"

cd "$ROOT"

i=0
while kill -0 "$RUNNER_PID" 2>/dev/null; do
    ts=$(date +%Y%m%d_%H%M%S)
    f="$SNAP_DIR/snap_${ts}.txt"
    {
        echo "=== snapshot $ts (i=$i) ==="
        echo "=== runner PID $RUNNER_PID alive; etime: $(ps -p $RUNNER_PID -o etime= 2>/dev/null || echo 'gone') ==="
        echo
        "$PYBIN" "$ROOT/scripts/analyze_kimi_run.py" \
            --trade-date 2026-05-23 \
            --since-ts "$SINCE_TS" \
            --max-rows 200 2>&1 || true
    } > "$f"
    i=$((i+1))
    sleep 360  # 6 minutes
done

# Final snapshot once the runner is gone
ts=$(date +%Y%m%d_%H%M%S)
f="$SNAP_DIR/snap_FINAL_${ts}.txt"
{
    echo "=== FINAL snapshot $ts ==="
    echo "=== runner PID $RUNNER_PID gone ==="
    echo
    "$PYBIN" "$ROOT/scripts/analyze_kimi_run.py" \
        --trade-date 2026-05-23 \
        --since-ts "$SINCE_TS" \
        --max-rows 1000 2>&1 || true
} > "$f"
echo "poller done; final at $f"
