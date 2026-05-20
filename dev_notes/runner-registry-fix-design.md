# Runner registry sync fix — design

**Status:** approved (2026-05-20)
**Author:** brainstorm session
**Related code:** `app/tasks.py`, `app/api.py`, `frontend/components/ActiveRunRow.vue`

## Problem

Two failures observed on 2026-05-20:

1. **Accidental double-launch.** Operator clicked the Launch button twice; both
   spawned a full S&P 100 batch (PIDs 67615 + 67625). The current
   `MAX_PER_KIND = {"manual": 2}` cap allowed both. Result: 2× compute cost
   for the same workload, cross-process race on `trading_memory.md` (the
   in-process `threading.Lock` we added in `6b218a5` does not synchronise
   across separate Python processes).
2. **Registry desync.** Of the two running processes, `active_tasks.json`
   only listed one (PID 67625). The UI's "Active runs" panel rendered a
   single row. The user had no way to see the second running process
   without `ps aux | grep app.runner`.

The desync root cause is in `app/tasks.py:_is_alive`:

```python
def _is_alive(pid):
    proc = _PROC_HANDLES.get(pid)   # in-memory dict, uvicorn-process-local
    if proc is not None:
        return proc.poll() is None
    via_ps = _pid_state_via_ps(pid)  # fallback
    return bool(via_ps) if via_ps is not None else False
```

`_PROC_HANDLES` is a module-level dict that lives only inside the uvicorn
process. When uvicorn restarts (frequent during the 2026-05-20 development
session: settings/retry/lock/max_tokens patches all forced restarts), the
dict is wiped while the runner subprocesses survive (they have
`start_new_session=True`). The next `list_active()` → `_prune()` call
falls back to `_pid_state_via_ps`, which is racy under load and may return
`None` ("unexpected error"), causing `_is_alive` to return `False`, and the
entry to be silently dropped from `active_tasks.json` with no recovery path.

## Goals

* Operator double-click cannot launch two identical batches.
* If two processes are running for any reason (intentional, scheduler
  overlap, CLI bypass), UI shows both.
* Registry state is provably consistent with OS state after any sequence
  of uvicorn restarts, process kills, or partial writes — no "auto-detect
  + flag" patchwork.

## Non-goals

* Cross-machine coordination (single-host only).
* Replacing the existing scheduled-vs-manual `MAX_PER_KIND` cap.
* Idempotency tokens / exactly-once semantics. The 15s time window is
  sufficient for the observed failure mode.

---

## Design

### Section 1 — OS-backed registry (root-cause fix)

**Principle change.** OS process state is the source of truth.
`active_tasks.json` is demoted to a metadata cache: it stores ticker list,
kind, started_at, log_path, model — but no longer claims "existence".

**`list_active()` becomes:**

1. Scan OS for processes matching the runner command pattern
   (`python -m app.runner ...`):
   * Use `ps -A -o pid=,etime=,stat=,command=` once per call.
   * Filter for rows whose `command` column contains `app.runner`.
   * Drop rows whose `stat` starts with `Z` (zombie).
2. Load `active_tasks.json` as a `pid → metadata` lookup table.
3. For each OS-discovered live PID:
   * If PID exists in JSON → emit the joined record (kind from JSON).
   * If PID is **not** in JSON → emit an "orphan" record with
     `kind="orphan"`, ticker list parsed from the command line, started_at
     derived from `ps etime`, model recorded as `null`.
4. For each JSON entry whose PID is **not** in the live OS list → drop it
   (log at debug level; the process is gone, no operator action needed).
5. Persist the reconciled list back to `active_tasks.json` so concurrent
   readers don't re-do the scan.

**`_PROC_HANDLES` stays** but only as a perf cache for SIGTERM dispatch in
`stop_run()` — never used for liveness decisions. If a stop request
arrives for a PID not in `_PROC_HANDLES`, we send `SIGTERM` via `os.kill`
directly (which works as long as the user owns the process).

**Command-line parser.** Tickers and `-j` workers count are parsed from
the `cmd` field. Robust against future flag additions by anchoring on the
`-m app.runner` marker:

```python
def _parse_runner_cmdline(cmd: str) -> tuple[list[str], int | None]:
    """Returns (tickers, workers) from a runner subprocess command line."""
    parts = shlex.split(cmd)
    if "app.runner" not in parts:
        return [], None
    i = parts.index("app.runner") + 1
    workers = None
    tickers: list[str] = []
    while i < len(parts):
        token = parts[i]
        if token == "-j" or token == "--workers":
            workers = int(parts[i + 1])
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        tickers.append(token)
        i += 1
    return tickers, workers
```

**uvicorn restart behaviour.** On import of `app.tasks`, the next
`list_active()` call (triggered by the first API request) repopulates the
JSON from OS state. No explicit "recover orphans on startup" step needed —
the existing read path is self-healing.

### Section 2 — 15-second launch debounce

**Location.** `app/tasks.py:start_run()`, after the capacity check, before
spawn.

**Implementation.**

```python
DEDUP_WINDOW_SECONDS = int(
    os.environ.get("TRADINGAGENTS_APP_DEDUP_WINDOW_SECONDS", "15")
)


class RecentDuplicateLaunch(RuntimeError):
    def __init__(self, gap_seconds: float, window: int):
        super().__init__(
            f"A run started {gap_seconds:.1f}s ago. "
            f"Please wait {window - gap_seconds:.0f}s before launching another."
        )
        self.gap_seconds = gap_seconds
        self.window = window


# inside start_run, after capacity check:
now = datetime.now(timezone.utc).replace(tzinfo=None)
for task in list_active():  # OS-backed
    started = datetime.fromisoformat(task["started_at"])
    gap = (now - started).total_seconds()
    if gap < DEDUP_WINDOW_SECONDS:
        raise RecentDuplicateLaunch(gap, DEDUP_WINDOW_SECONDS)
```

**Why "any active task within 15s" rather than "same payload within 15s".**
Per the brainstorm: the user picked option D (time window, payload-
agnostic). Double-click protection doesn't need payload comparison; if the
user genuinely wants two different batches they can wait 15s. The simpler
rule is fewer lines and zero edge cases.

**API layer.** `app/api.py` catches `RecentDuplicateLaunch` and returns:

```python
raise HTTPException(status_code=409, detail=str(exc))
```

409 Conflict is the most precise status — the request conflicts with the
current resource state (a recent in-flight launch). FastAPI's default
error envelope (`{"detail": "..."}`) is already what the frontend's
`errorMessage()` extracts.

### Section 3 — Frontend (minimal)

Two changes in `frontend/`:

1. **`ActiveRunRow.vue`** — render `kind="orphan"` with an amber badge
   labelled exactly **`auto-detected`**. Existing `manual`/`scheduled`
   badges stay unchanged.
2. **No changes needed** for: 409 error toast (existing `errorMessage()`
   picks up `detail`), multi-row rendering (existing `v-for` over
   `active`), launch-button-while-launching guard (existing `:disabled`).

---

## Edge cases

| Case | Behaviour |
|------|-----------|
| uvicorn restart mid-batch | New uvicorn `list_active()` scans OS, finds the runner, rebuilds JSON entry with orphan→original-kind upgrade if JSON had metadata. |
| `ps` returns garbage / OS scan fails | Treat scan as authoritative; if the scan command itself errored, log + return current JSON unchanged (degrade gracefully). |
| Two launches < 15s on different uvicorn processes | Each uvicorn's `start_run` reads the same `active_tasks.json` via `list_active`; OS scan finds the first runner; window check rejects the second. Cross-uvicorn safety achieved without a shared lock. |
| Runner started from CLI (`python -m app.runner ...`) | OS scan picks it up as orphan; UI shows it with the auto-detected badge. |
| Kimi-K2.6 model name has slash (e.g. `moonshotai/Kimi-K2.6`) | `shlex.split` handles slashes; tickers don't contain slashes — parser is unambiguous. |
| Zombie PID still in JSON after kill | OS scan with `ps -p PID -o stat=` returns `Z`; we treat `Z` as "not alive" and drop from JSON. |

---

## Testing

* **Unit** (`tests/test_tasks_registry.py`, new):
  * `_parse_runner_cmdline` round-trips: `["-j", "16", "AAPL", "MSFT"]`
    → `(["AAPL", "MSFT"], 16)`.
  * Window dedup: synthetic active task with `started_at = now - 5s`
    raises `RecentDuplicateLaunch`; `started_at = now - 20s` does not.
  * Orphan reconstruction: mock `_ps_scan` to return a PID, JSON empty
    → `list_active` returns one entry with `kind="orphan"`.
  * JSON stale entry: JSON has PID X, OS scan does not → entry dropped,
    JSON rewritten.

* **Integration** (`tests/test_tasks_lifecycle.py`, extended):
  * Spawn a real `python -c "import time; time.sleep(60)"` as a stand-in,
    verify it shows up in `list_active()`.
  * Simulate uvicorn restart by clearing `_PROC_HANDLES`, re-call
    `list_active()`, verify the spawned process is still listed.

* **Manual smoke**:
  * Click Launch twice within 5s in the UI → second click shows 409 toast.
  * Kill uvicorn (`pkill -f uvicorn`), restart, observe Active runs
    panel still shows the in-flight runner (now flagged orphan if metadata
    lost, or normal if recovered).

---

## Rollout

1. Code changes land on `gonka-tradeagents-kimi/v1-nfrontend` branch.
2. uvicorn restart required (because `app.tasks` is loaded into the
   uvicorn process).
3. Existing in-flight runners are not affected — they keep running; the
   first post-deploy `list_active` call reconciles them.
4. No DB migration needed.

## Backwards compatibility

* `active_tasks.json` schema is **forward-compatible** — we only add
  `kind="orphan"` as a new enum value, existing entries are still parsed.
* `start_run()` API signature unchanged.
* `MAX_PER_KIND` cap unchanged (still `{manual: 2, scheduled: 1}`); the
  15s window is a separate orthogonal gate.
