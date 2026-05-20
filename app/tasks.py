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


# Re-entrant: ``_prune`` holds the lock while iterating, and the helpers
# it calls (``_is_alive``) also acquire the same lock to read the handle
# dict. A plain ``Lock`` deadlocks the second acquire on the same thread.
_PROC_LOCK = threading.RLock()
_PROC_HANDLES: dict[int, subprocess.Popen] = {}

# Concurrency caps. Manual runs and scheduled runs are tracked as
# separate kinds so the scheduler can always claim its single slot even
# while the operator is launching ad-hoc batches.
MAX_PER_KIND = {"manual": 2, "scheduled": 1}
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


def _pid_state_via_ps(pid: int) -> Optional[bool]:
    """Linux + macOS-compatible probe; ``None`` on unexpected errors."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return False
    state = result.stdout.strip()
    if not state:
        return False
    return not state.startswith("Z")


def _is_alive(pid: int) -> bool:
    """Authoritative liveness: poll the handle when we have one, else ps."""
    with _PROC_LOCK:
        proc = _PROC_HANDLES.get(pid)
    if proc is not None:
        return proc.poll() is None
    via_ps = _pid_state_via_ps(pid)
    return bool(via_ps) if via_ps is not None else False


def _prune(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alive: list[dict[str, Any]] = []
    with _PROC_LOCK:
        for task in tasks:
            if _is_alive(task["pid"]):
                alive.append(task)
            else:
                _PROC_HANDLES.pop(task["pid"], None)
    return alive


def list_active() -> list[dict[str, Any]]:
    """Return the live set, with any zombie entries pruned and persisted."""
    tasks = _prune(_load_index())
    _save_index(tasks)
    return tasks


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
        tasks_now = _prune(_load_index())
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
