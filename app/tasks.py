"""Background-task management for the FastAPI backend.

Owns the registry of active ``python -m app.runner`` subprocesses, the
on-disk task index, and the log-tail readback. Extracted from the old
Streamlit dashboard so the HTTP server can manage runs across multiple
client connections without losing its ``Popen`` handles.

The module is process-local: it lives in the uvicorn worker that handled
the launch. Restarting the API server re-discovers active tasks via
``ps`` against the on-disk index.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .settings_store import APP_HOME, settings_to_env


_ACTIVE_TASKS_PATH = APP_HOME / "active_tasks.json"
_LOG_DIR = APP_HOME / "logs"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_runner_cmdline(cmd: str) -> tuple[list[str], Optional[int]]:
    """Extract (tickers, workers) from a ``python -m app.runner ...`` line.

    Returns ``([], None)`` if the command line doesn't look like a runner
    invocation. Recognises ``-j N`` and ``--workers N`` as the workers
    flag; treats every other dash-prefixed token as an unknown flag to
    skip (no value consumed unless we know about it).
    """
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return [], None
    if "app.runner" not in parts:
        return [], None
    i = parts.index("app.runner") + 1
    workers: Optional[int] = None
    tickers: list[str] = []
    while i < len(parts):
        token = parts[i]
        if token in ("-j", "--workers") and i + 1 < len(parts):
            try:
                workers = int(parts[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        tickers.append(token)
        i += 1
    return tickers, workers


def _parse_etime(etime: str) -> "timedelta":
    """Convert ``ps -o etime=`` output into a ``timedelta``.

    Recognised forms (``man ps``):

    * ``MM:SS``        — less than one hour
    * ``HH:MM:SS``     — less than one day
    * ``dd-HH:MM:SS``  — at least one day

    Returns ``timedelta(0)`` for unparseable strings rather than raising,
    so a single weird row from ``ps`` doesn't blow up the whole scan.
    """
    from datetime import timedelta
    cleaned = etime.strip()
    days = 0
    if "-" in cleaned:
        d_str, cleaned = cleaned.split("-", 1)
        try:
            days = int(d_str)
        except ValueError:
            return timedelta(0)
    parts = cleaned.split(":")
    try:
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return timedelta(days=days, minutes=m, seconds=s)
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return timedelta(days=days, hours=h, minutes=m, seconds=s)
    except ValueError:
        return timedelta(0)
    return timedelta(0)


def _utc_now_naive() -> datetime:
    """Wrapper so tests can monkeypatch the clock."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run_ps_scan() -> Optional[str]:
    """Run ``ps -A -o pid,etime,stat,command`` and return stdout.

    Wrapped in a tiny function so tests can patch a single seam without
    monkeypatching ``subprocess.run`` globally. Returns ``None`` on any
    failure so the caller can degrade to "no scan available".
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,etime=,stat=,command="],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _scan_runner_processes() -> list[dict[str, Any]]:
    """Return one dict per live runner subprocess.

    Each dict has: ``pid: int``, ``started_at: str (ISO, UTC naive)``,
    ``tickers: list[str]``, ``workers: Optional[int]``. Zombie processes
    (``stat`` starts with ``Z``) are dropped because they don't actually
    consume the slot anymore — only their PID does, and only briefly.

    The OS-backed source of truth for liveness; everything else in this
    module joins onto its output by PID.
    """
    raw = _run_ps_scan()
    if not raw:
        return []
    now = _utc_now_naive()
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: "PID ETIME STAT COMMAND..."
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_str, etime_str, stat, cmd = parts
        if stat.startswith("Z"):
            continue
        if "app.runner" not in cmd:
            continue
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        tickers, workers = _parse_runner_cmdline(cmd)
        started_at = (now - _parse_etime(etime_str)).isoformat(timespec="seconds")
        rows.append({
            "pid": pid,
            "started_at": started_at,
            "tickers": tickers,
            "workers": workers,
        })
    return rows


# Re-entrant: ``_prune`` holds the lock while iterating, and the helpers
# it calls (``_is_alive``) also acquire the same lock to read the handle
# dict. A plain ``Lock`` deadlocks the second acquire on the same thread.
_PROC_LOCK = threading.RLock()
_PROC_HANDLES: dict[int, subprocess.Popen] = {}

# Concurrency caps. Manual runs and scheduled runs are tracked as
# separate kinds so the scheduler can always claim its single slot even
# while the operator is launching ad-hoc batches. As of 2026-05-20 both
# are capped at 1: parallel manual runs caused duplicate-launch incidents
# (see dev_notes/runner-registry-fix-design.md). Operators who want
# parallel A/B testing should wait for the first to finish or bump this
# constant in a hotfix.
MAX_PER_KIND = {"manual": 1, "scheduled": 1}
KINDS = tuple(MAX_PER_KIND.keys())


class CapacityExceeded(RuntimeError):
    """Raised when ``start_run`` is invoked beyond the per-kind limit."""

    def __init__(self, kind: str, current: int, limit: int) -> None:
        super().__init__(
            f"{kind} capacity exhausted: {current}/{limit} already running"
        )
        self.kind = kind
        self.current = current
        self.limit = limit


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _load_index() -> list[dict[str, Any]]:
    if not _ACTIVE_TASKS_PATH.exists():
        return []
    try:
        return json.loads(_ACTIVE_TASKS_PATH.read_text())
    except json.JSONDecodeError:
        return []


def _save_index(tasks: list[dict[str, Any]]) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = _ACTIVE_TASKS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tasks, indent=2))
    tmp.replace(_ACTIVE_TASKS_PATH)


def list_active() -> list[dict[str, Any]]:
    """Reconcile OS process state with the on-disk metadata cache.

    OS scan is the source of truth: every live runner subprocess
    surfaces here, regardless of whether the uvicorn that started it is
    still around. ``active_tasks.json`` provides extra metadata
    (kind, log_path, mode, deep_model) — joined by PID.

    Side effects:
    * PIDs in JSON with no matching live process → dropped silently.
    * PIDs in the OS scan with no JSON entry → emitted with
      ``kind="orphan"`` and metadata reconstructed from the command line.
    * The reconciled list is written back to JSON so concurrent readers
      don't re-do the scan, and so the cache stays roughly fresh.
    """
    scan_rows = _scan_runner_processes()
    metadata_by_pid = {t["pid"]: t for t in _load_index()}

    reconciled: list[dict[str, Any]] = []
    for row in scan_rows:
        pid = row["pid"]
        meta = metadata_by_pid.get(pid)
        if meta is not None:
            # Prefer JSON's started_at (more precise — recorded at spawn,
            # not derived from ``etime`` rounding) but trust the scan for
            # liveness. Fill any gaps with scan data.
            reconciled.append({
                **meta,
                "tickers": meta.get("tickers") or row["tickers"],
                "workers": meta.get("workers") or row["workers"],
            })
        else:
            reconciled.append({
                "pid": pid,
                "kind": "orphan",
                "started_at": row["started_at"],
                "tickers": row["tickers"],
                "workers": row["workers"],
                "log_path": None,
                "mode": None,
                "deep_model": None,
            })

    # Persist the reconciled state. Acquire the lock so concurrent
    # start_run calls don't observe a half-written file.
    with _PROC_LOCK:
        _save_index(reconciled)
        # Drop _PROC_HANDLES entries for PIDs no longer in the scan —
        # the subprocess is gone so the handle is useless.
        live_pids = {r["pid"] for r in reconciled}
        for stale_pid in [p for p in _PROC_HANDLES if p not in live_pids]:
            _PROC_HANDLES.pop(stale_pid, None)

    return reconciled


def count_active_by_kind() -> dict[str, int]:
    """How many slots each kind is currently occupying."""
    counts = {kind: 0 for kind in KINDS}
    for task in list_active():
        kind = task.get("kind", "manual")
        if kind in counts:
            counts[kind] += 1
        else:
            counts.setdefault(kind, 0)
            counts[kind] += 1
    return counts


def start_run(
    *,
    settings: dict[str, Any],
    tickers: list[str],
    workers: int,
    kind: str = "manual",
) -> dict[str, Any]:
    """Launch ``python -m app.runner`` as a detached child process group.

    ``kind`` is either ``"manual"`` (operator click) or ``"scheduled"``
    (BackgroundScheduler fire). Capacity is enforced per-kind so the
    scheduler always has a slot to claim regardless of how many ad-hoc
    runs the operator has queued.

    ``start_new_session=True`` puts the runner in its own process group so
    a Stop request can ``killpg(SIGTERM)`` the whole tree, including the
    worker threads' subprocesses.

    Raises:
        CapacityExceeded: when the kind is already at its concurrency cap.
    """
    if kind not in MAX_PER_KIND:
        raise ValueError(f"Unknown run kind: {kind!r}")

    # Capacity check + launch happen under the same lock so two
    # near-simultaneous POSTs can't both squeeze past the limit.
    with _PROC_LOCK:
        counts = count_active_by_kind()
        limit = MAX_PER_KIND[kind]
        if counts.get(kind, 0) >= limit:
            raise CapacityExceeded(kind, counts.get(kind, 0), limit)

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc).replace(tzinfo=None)
        stamp = started.strftime("%Y%m%d_%H%M%S")
        log_path = _LOG_DIR / f"run_{stamp}_{os.getpid()}.log"
        cmd = [sys.executable, "-m", "app.runner", "-j", str(workers), *tickers]
        env = settings_to_env(settings)
        log_file = open(log_path, "w")  # closed by subprocess on exit
        proc = subprocess.Popen(
            cmd,
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _PROC_HANDLES[proc.pid] = proc

        task = {
            "pid": proc.pid,
            "kind": kind,
            "started_at": started.isoformat(timespec="seconds"),
            "tickers": tickers,
            "workers": workers,
            "log_path": str(log_path),
            "mode": settings["mode"],
            "deep_model": settings.get("deep_model"),
        }
        tasks_now = list_active()
        tasks_now.append(task)
        _save_index(tasks_now)
        return task


def stop_run(pid: int) -> bool:
    """Send SIGTERM to the runner's process group."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_log_tail(path: str, n: int = 30) -> str:
    try:
        with open(path) as fh:
            return "".join(fh.readlines()[-n:])
    except OSError:
        return ""


def task_with_runtime(task: dict[str, Any]) -> dict[str, Any]:
    """Enrich a stored task entry with elapsed seconds for the API surface."""
    started_at = datetime.fromisoformat(task["started_at"])
    elapsed = (datetime.utcnow() - started_at).total_seconds()
    return {**task, "elapsed_seconds": int(elapsed)}
