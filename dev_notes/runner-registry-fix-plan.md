# Runner Registry Sync Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the runner task registry derive from OS state (not in-memory `_PROC_HANDLES`), tighten `MAX_PER_KIND` to 1 per kind, and reject launches within a 15-second window — eliminating accidental double-launches and registry/UI desync after uvicorn restart.

**Architecture:** OS process scan via `ps -A -o pid,etime,stat,command` becomes the source of truth for "what's running". `active_tasks.json` is demoted to a metadata cache joined onto the OS scan by PID. A new `RecentDuplicateLaunch` exception fires from `start_run` when any active task started within 15s of the new launch. Frontend gains a third `orphan` badge for OS-discovered runs missing metadata. Spec: `dev_notes/runner-registry-fix-design.md`.

**Tech Stack:** Python 3.11 (FastAPI + uvicorn backend), Vue 3 / Nuxt 3 frontend, pytest, `subprocess.run` for `ps`, `shlex` for command-line parsing.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `app/tasks.py` | Modify | Replace `_is_alive`/`_prune`/`list_active` with OS-backed reconciler; add `_parse_runner_cmdline`, `_parse_etime`, `_scan_runner_processes`, `RecentDuplicateLaunch`; tighten `MAX_PER_KIND`; add 15s window check in `start_run` |
| `app/api.py` | Modify | Catch `RecentDuplicateLaunch` → return HTTP 409 |
| `tests/test_tasks_registry.py` | Create | Unit tests for new helpers + `list_active` reconciliation + window dedup |
| `frontend/components/ActiveRunRow.vue` | Modify | Add amber `auto-detected` badge for `kind="orphan"` |

---

## Task 1: Add `_parse_runner_cmdline` helper

**Files:**
- Modify: `app/tasks.py` (add helper near top, after `_REPO_ROOT`)
- Create: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tasks_registry.py`:

```python
"""Tests for OS-backed task registry helpers in app.tasks."""

from __future__ import annotations

from app.tasks import _parse_runner_cmdline


def test_parse_runner_cmdline_with_workers_and_tickers():
    cmd = "/opt/conda/bin/python3.11 -m app.runner -j 16 AAPL MSFT NVDA"
    tickers, workers = _parse_runner_cmdline(cmd)
    assert tickers == ["AAPL", "MSFT", "NVDA"]
    assert workers == 16


def test_parse_runner_cmdline_long_form_workers_flag():
    cmd = "python -m app.runner --workers 4 BRK.B GOOGL"
    tickers, workers = _parse_runner_cmdline(cmd)
    assert tickers == ["BRK.B", "GOOGL"]
    assert workers == 4


def test_parse_runner_cmdline_no_workers_flag():
    cmd = "python3 -m app.runner TSLA"
    tickers, workers = _parse_runner_cmdline(cmd)
    assert tickers == ["TSLA"]
    assert workers is None


def test_parse_runner_cmdline_ignores_unrelated_flags():
    cmd = "python -m app.runner --debug -j 8 NVDA"
    tickers, workers = _parse_runner_cmdline(cmd)
    assert tickers == ["NVDA"]
    assert workers == 8


def test_parse_runner_cmdline_returns_empty_for_non_runner():
    cmd = "/opt/conda/bin/python3.11 -m app.scheduler"
    tickers, workers = _parse_runner_cmdline(cmd)
    assert tickers == []
    assert workers is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tasks_registry.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_runner_cmdline'`

- [ ] **Step 3: Add the helper to `app/tasks.py`**

Add `import shlex` to the top of `app/tasks.py` (next to existing imports). Then add this helper after the `_REPO_ROOT` line (~line 31):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tasks_registry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): add _parse_runner_cmdline for OS scan path"
```

---

## Task 2: Add `_parse_etime` helper

**Files:**
- Modify: `app/tasks.py` (add helper)
- Modify: `tests/test_tasks_registry.py` (add tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
from datetime import timedelta
from app.tasks import _parse_etime


def test_parse_etime_seconds_only_format():
    assert _parse_etime("00:05") == timedelta(minutes=0, seconds=5)


def test_parse_etime_minutes_seconds():
    assert _parse_etime("17:42") == timedelta(minutes=17, seconds=42)


def test_parse_etime_hours_minutes_seconds():
    assert _parse_etime("01:23:45") == timedelta(hours=1, minutes=23, seconds=45)


def test_parse_etime_days_hours_minutes_seconds():
    assert _parse_etime("3-04:05:06") == timedelta(
        days=3, hours=4, minutes=5, seconds=6
    )


def test_parse_etime_with_leading_whitespace():
    assert _parse_etime("  00:05") == timedelta(seconds=5)


def test_parse_etime_returns_zero_on_garbage():
    assert _parse_etime("not-a-time") == timedelta(0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tasks_registry.py::test_parse_etime_seconds_only_format -v`
Expected: FAIL with `ImportError: cannot import name '_parse_etime'`

- [ ] **Step 3: Add the helper to `app/tasks.py`**

Add this below `_parse_runner_cmdline`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tasks_registry.py -v -k parse_etime`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): add _parse_etime to convert ps etime to timedelta"
```

---

## Task 3: Add `_scan_runner_processes` OS-scan helper

**Files:**
- Modify: `app/tasks.py`
- Modify: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
from unittest.mock import patch
from datetime import datetime, timezone


def _fake_ps_output(rows: list[tuple[str, str, str, str]]) -> str:
    """Build a fake `ps -A -o pid=,etime=,stat=,command=` block."""
    return "\n".join(f"{pid} {etime} {stat} {cmd}" for pid, etime, stat, cmd in rows)


def test_scan_runner_processes_filters_to_runners():
    fake = _fake_ps_output([
        ("100", "00:05", "S", "/usr/bin/python -m app.runner -j 8 NVDA"),
        ("200", "10:00", "R", "/usr/bin/python -m app.scheduler"),
        ("300", "01:00", "S", "/usr/bin/bash -c 'echo hi'"),
    ])
    with patch("app.tasks._run_ps_scan", return_value=fake):
        rows = _scan_runner_processes()
    assert len(rows) == 1
    assert rows[0]["pid"] == 100
    assert rows[0]["tickers"] == ["NVDA"]
    assert rows[0]["workers"] == 8


def test_scan_runner_processes_drops_zombies():
    fake = _fake_ps_output([
        ("100", "00:05", "S", "/usr/bin/python -m app.runner AAPL"),
        ("101", "00:06", "Z+", "/usr/bin/python -m app.runner MSFT"),
    ])
    with patch("app.tasks._run_ps_scan", return_value=fake):
        rows = _scan_runner_processes()
    pids = [r["pid"] for r in rows]
    assert 100 in pids
    assert 101 not in pids


def test_scan_runner_processes_computes_started_at_iso():
    """etime=01:00 means the process has been alive 1 minute."""
    fake = _fake_ps_output([
        ("100", "01:00", "S", "/usr/bin/python -m app.runner NVDA"),
    ])
    fixed_now = datetime(2026, 5, 20, 12, 0, 0)
    with patch("app.tasks._run_ps_scan", return_value=fake), \
         patch("app.tasks._utc_now_naive", return_value=fixed_now):
        rows = _scan_runner_processes()
    assert rows[0]["started_at"] == "2026-05-20T11:59:00"


def test_scan_runner_processes_returns_empty_on_ps_failure():
    with patch("app.tasks._run_ps_scan", return_value=None):
        assert _scan_runner_processes() == []


# Make the helpers importable in this test module
from app.tasks import _scan_runner_processes
```

Also add this import line at the top of the test module (next to other imports):

```python
from app.tasks import _scan_runner_processes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tasks_registry.py::test_scan_runner_processes_filters_to_runners -v`
Expected: FAIL with `ImportError: cannot import name '_scan_runner_processes'`

- [ ] **Step 3: Add helpers to `app/tasks.py`**

Add below `_parse_etime`:

```python
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
        if not tickers:
            # ``-m app.runner`` with no positional args — could be a
            # zero-ticker invocation; still surface it but with empty
            # list so the operator can see it in the UI.
            tickers = []
        started_at = (now - _parse_etime(etime_str)).isoformat(timespec="seconds")
        rows.append({
            "pid": pid,
            "started_at": started_at,
            "tickers": tickers,
            "workers": workers,
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tasks_registry.py -v`
Expected: PASS (all green)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): add _scan_runner_processes for OS-backed liveness"
```

---

## Task 4: Rewrite `list_active` to reconcile OS scan with JSON

**Files:**
- Modify: `app/tasks.py`
- Modify: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_active_tasks_path(tmp_path, monkeypatch):
    """Point the module's active_tasks.json at a per-test temp file."""
    fake_path = tmp_path / "active_tasks.json"
    monkeypatch.setattr("app.tasks._ACTIVE_TASKS_PATH", fake_path)
    return fake_path


def test_list_active_returns_known_kind_when_pid_in_json(tmp_active_tasks_path):
    fake_scan = [{
        "pid": 100, "started_at": "2026-05-20T11:00:00",
        "tickers": ["NVDA"], "workers": 8,
    }]
    tmp_active_tasks_path.write_text(json.dumps([{
        "pid": 100,
        "kind": "manual",
        "started_at": "2026-05-20T11:00:00",
        "tickers": ["NVDA"],
        "workers": 8,
        "log_path": "/tmp/run.log",
        "mode": "router",
        "deep_model": "Qwen/Qwen3",
    }]))
    with patch("app.tasks._scan_runner_processes", return_value=fake_scan):
        active = list_active()
    assert len(active) == 1
    assert active[0]["kind"] == "manual"
    assert active[0]["deep_model"] == "Qwen/Qwen3"


def test_list_active_marks_unknown_pids_as_orphan(tmp_active_tasks_path):
    """OS sees a runner; JSON has no metadata → orphan entry."""
    fake_scan = [{
        "pid": 200, "started_at": "2026-05-20T11:00:00",
        "tickers": ["MSFT", "AAPL"], "workers": 4,
    }]
    tmp_active_tasks_path.write_text("[]")
    with patch("app.tasks._scan_runner_processes", return_value=fake_scan):
        active = list_active()
    assert len(active) == 1
    assert active[0]["kind"] == "orphan"
    assert active[0]["tickers"] == ["MSFT", "AAPL"]
    assert active[0]["workers"] == 4
    assert active[0]["deep_model"] is None


def test_list_active_drops_json_entries_with_no_live_pid(tmp_active_tasks_path):
    """JSON has stale entry; OS scan empty → that entry is dropped."""
    tmp_active_tasks_path.write_text(json.dumps([{
        "pid": 999, "kind": "manual", "started_at": "2026-05-20T10:00:00",
        "tickers": ["X"], "workers": 1, "log_path": "/tmp/x.log",
        "mode": "router", "deep_model": "Q",
    }]))
    with patch("app.tasks._scan_runner_processes", return_value=[]):
        active = list_active()
    assert active == []
    # Reconciled state was persisted back
    assert json.loads(tmp_active_tasks_path.read_text()) == []


from app.tasks import list_active
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tasks_registry.py::test_list_active_marks_unknown_pids_as_orphan -v`
Expected: FAIL — the new orphan / reconciliation behaviour is not yet there. The test will likely fail with the wrong `kind` value or different shape.

- [ ] **Step 3: Replace the implementations in `app/tasks.py`**

Delete the existing `_is_alive`, `_prune`, and `list_active`. Replace with:

```python
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
```

Also remove the now-unused `_pid_state_via_ps` (was the dead fallback). Search for its callers first — if grep shows only the old `_is_alive`, delete it. Else leave it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tasks_registry.py -v`
Expected: PASS (all green, including the 3 new list_active tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): list_active reconciles OS scan with JSON metadata"
```

---

## Task 5: Tighten `MAX_PER_KIND` to 1 per kind

**Files:**
- Modify: `app/tasks.py`
- Modify: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
from app.tasks import MAX_PER_KIND


def test_max_per_kind_caps_manual_at_one():
    assert MAX_PER_KIND["manual"] == 1


def test_max_per_kind_caps_scheduled_at_one():
    assert MAX_PER_KIND["scheduled"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tasks_registry.py -v -k max_per_kind`
Expected: FAIL — `MAX_PER_KIND["manual"]` is currently 2.

- [ ] **Step 3: Change the constant in `app/tasks.py`**

Find the line `MAX_PER_KIND = {"manual": 2, "scheduled": 1}` and change to:

```python
# Concurrency caps. Manual runs and scheduled runs are tracked as
# separate kinds so the scheduler can always claim its single slot even
# while the operator is launching ad-hoc batches. As of 2026-05-20 both
# are capped at 1: parallel manual runs caused duplicate-launch incidents
# (see dev_notes/runner-registry-fix-design.md). Operators who want
# parallel A/B testing should wait for the first to finish or bump this
# constant in a hotfix.
MAX_PER_KIND = {"manual": 1, "scheduled": 1}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tasks_registry.py -v -k max_per_kind`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): tighten MAX_PER_KIND to 1 per kind"
```

---

## Task 6: Add `RecentDuplicateLaunch` + 15-second window check

**Files:**
- Modify: `app/tasks.py`
- Modify: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
from app.tasks import (
    RecentDuplicateLaunch,
    _dedup_window_seconds,
    _check_recent_launch,
)


def test_recent_duplicate_launch_carries_gap_and_window():
    exc = RecentDuplicateLaunch(gap_seconds=3.2, window=15)
    assert exc.gap_seconds == 3.2
    assert exc.window == 15
    assert "3.2s ago" in str(exc)
    assert "11s" in str(exc) or "12s" in str(exc)  # 15 - 3.2 rounded


def test_check_recent_launch_raises_when_inside_window():
    active = [{
        "pid": 1, "started_at": "2026-05-20T11:59:55", "kind": "manual",
        "tickers": [], "workers": 1, "log_path": None,
        "mode": None, "deep_model": None,
    }]
    fixed_now = datetime(2026, 5, 20, 12, 0, 0)  # 5s after launch
    with patch("app.tasks._utc_now_naive", return_value=fixed_now):
        with pytest.raises(RecentDuplicateLaunch) as exc:
            _check_recent_launch(active, window_seconds=15)
    assert 4.5 < exc.value.gap_seconds < 5.5


def test_check_recent_launch_allows_when_outside_window():
    active = [{
        "pid": 1, "started_at": "2026-05-20T11:00:00", "kind": "manual",
        "tickers": [], "workers": 1, "log_path": None,
        "mode": None, "deep_model": None,
    }]
    fixed_now = datetime(2026, 5, 20, 12, 0, 0)  # 1 hour after
    with patch("app.tasks._utc_now_naive", return_value=fixed_now):
        _check_recent_launch(active, window_seconds=15)  # no raise


def test_check_recent_launch_no_active_no_raise():
    fixed_now = datetime(2026, 5, 20, 12, 0, 0)
    with patch("app.tasks._utc_now_naive", return_value=fixed_now):
        _check_recent_launch([], window_seconds=15)


def test_dedup_window_seconds_default():
    assert _dedup_window_seconds() == 15


def test_dedup_window_seconds_env_override(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DEDUP_WINDOW_SECONDS", "30")
    assert _dedup_window_seconds() == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tasks_registry.py -v -k 'recent_duplicate or check_recent or dedup_window'`
Expected: FAIL with `ImportError: cannot import name 'RecentDuplicateLaunch'`

- [ ] **Step 3: Add exception + helpers + window check to `start_run` in `app/tasks.py`**

Add the exception and helper near the top of `app/tasks.py` (next to `CapacityExceeded`):

```python
class RecentDuplicateLaunch(RuntimeError):
    """Raised by ``start_run`` when a launch arrives inside the debounce window.

    Catches double-click / network-retry / cross-uvicorn race scenarios
    that the per-kind capacity check can let through.
    """

    def __init__(self, gap_seconds: float, window: int) -> None:
        remaining = max(0, window - gap_seconds)
        super().__init__(
            f"A run started {gap_seconds:.1f}s ago. "
            f"Please wait {remaining:.0f}s before launching another."
        )
        self.gap_seconds = gap_seconds
        self.window = window


def _dedup_window_seconds() -> int:
    """Read the debounce window from env each call so tests can monkeypatch."""
    raw = os.environ.get("TRADINGAGENTS_APP_DEDUP_WINDOW_SECONDS")
    if raw is None:
        return 15
    try:
        return max(0, int(raw))
    except ValueError:
        return 15


def _check_recent_launch(active: list[dict[str, Any]], *, window_seconds: int) -> None:
    """Raise ``RecentDuplicateLaunch`` if any active task started within window."""
    if window_seconds <= 0:
        return
    now = _utc_now_naive()
    for task in active:
        started = datetime.fromisoformat(task["started_at"])
        gap = (now - started).total_seconds()
        if 0 <= gap < window_seconds:
            raise RecentDuplicateLaunch(gap, window_seconds)
```

Then in `start_run`, after the existing capacity check and before spawning, add:

```python
        # ... existing capacity check ...
        _check_recent_launch(active, window_seconds=_dedup_window_seconds())
        # ... existing spawn logic ...
```

The `active` variable is whatever `list_active()` (or your `count_active_by_kind`) call returned — reuse it rather than calling `list_active()` again. Specifically, refactor:

```python
    with _PROC_LOCK:
        counts = count_active_by_kind()
        limit = MAX_PER_KIND[kind]
        if counts.get(kind, 0) >= limit:
            raise CapacityExceeded(kind, counts.get(kind, 0), limit)
```

into:

```python
    with _PROC_LOCK:
        active = list_active()
        counts = {k: 0 for k in KINDS}
        for t in active:
            counts[t.get("kind", "manual")] = counts.get(t.get("kind", "manual"), 0) + 1
        limit = MAX_PER_KIND[kind]
        if counts.get(kind, 0) >= limit:
            raise CapacityExceeded(kind, counts.get(kind, 0), limit)
        _check_recent_launch(active, window_seconds=_dedup_window_seconds())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tasks_registry.py -v`
Expected: PASS (all green)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/tasks.py
git commit -m "feat(tasks): reject launches inside 15s debounce window"
```

---

## Task 7: API layer catches `RecentDuplicateLaunch` → 409

**Files:**
- Modify: `app/api.py`
- Modify: `tests/test_tasks_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tasks_registry.py`:

```python
from fastapi.testclient import TestClient


def test_api_returns_409_on_recent_duplicate(monkeypatch):
    """Hitting POST /api/runs when a recent launch exists → 409."""
    from app.api import app
    from app import tasks as tasks_mod
    from app import settings_store

    # Make settings think we are fully configured (router mode + key)
    monkeypatch.setattr(
        settings_store,
        "load_settings",
        lambda: {
            "mode": "router",
            "router_api_key": "sk-test",
            "sdk_private_key": "",
            "sdk_source_url": "",
            "deep_model": "Qwen/Qwen3",
            "quick_model": "Qwen/Qwen3",
            "max_workers": 4,
        },
    )

    def fake_start_run(**kwargs):
        raise tasks_mod.RecentDuplicateLaunch(gap_seconds=3.0, window=15)

    monkeypatch.setattr(tasks_mod, "start_run", fake_start_run)

    client = TestClient(app)
    resp = client.post("/api/runs", json={"tickers": ["NVDA"], "workers": 4, "kind": "manual"})
    assert resp.status_code == 409
    assert "3.0s ago" in resp.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tasks_registry.py::test_api_returns_409_on_recent_duplicate -v`
Expected: FAIL — exception propagates as 500 because `app/api.py` doesn't catch it yet.

- [ ] **Step 3: Catch the exception in `app/api.py`**

Find the existing `except tasks.CapacityExceeded` block inside `start_run` (~line 209) and add a second except above it:

```python
    try:
        task = tasks.start_run(
            settings=settings,
            tickers=cleaned,
            workers=req.workers,
            kind=req.kind,
        )
    except tasks.RecentDuplicateLaunch as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except tasks.CapacityExceeded as exc:
        # 409 Conflict — the request was valid but the system is at the
        # per-kind cap. Frontend uses this to disable the Run button.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{exc.kind} capacity exhausted ({exc.current}/{exc.limit}). "
                "Wait for a slot or stop an existing run."
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tasks_registry.py::test_api_returns_409_on_recent_duplicate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tasks_registry.py app/api.py
git commit -m "feat(api): surface RecentDuplicateLaunch as HTTP 409"
```

---

## Task 8: Frontend — orphan badge in `ActiveRunRow.vue`

**Files:**
- Modify: `frontend/components/ActiveRunRow.vue`

(No unit test — Vue component visual change. Manual smoke test in Task 9 covers it.)

- [ ] **Step 1: Read the current badge block**

Open `frontend/components/ActiveRunRow.vue`. The existing kind badge is around line 8–17:

```vue
<span
  class="inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide"
  :class="
    task.kind === 'scheduled'
      ? 'border-indigo-500/40 bg-indigo-500/10 text-indigo-200'
      : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
  "
>
  {{ task.kind || "manual" }}
</span>
```

This is a binary scheduled / not-scheduled colour split. We need three branches.

- [ ] **Step 2: Replace with three-way conditional**

Replace the entire `<span ... >...</span>` block with:

```vue
<span
  class="inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide"
  :class="
    task.kind === 'scheduled'
      ? 'border-indigo-500/40 bg-indigo-500/10 text-indigo-200'
      : task.kind === 'orphan'
        ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
        : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
  "
>
  {{ task.kind === "orphan" ? "auto-detected" : task.kind || "manual" }}
</span>
```

- [ ] **Step 3: Verify Vite picks up the change**

Run (in a second terminal, leave it open):
```bash
tmux capture-pane -t nuxt -p | tail -10
```
Expected: a "hmr update /components/ActiveRunRow.vue" line.

- [ ] **Step 4: Manual visual check**

Open http://127.0.0.1:3000/tasks in a browser. If no active orphan run exists, briefly test with a fake orphan by inserting a row into `~/.tradingagents/app/active_tasks.json` is unnecessary — just confirm `manual` / `scheduled` badges still render correctly. Task 9 covers the real orphan badge.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ActiveRunRow.vue
git commit -m "feat(ui): amber 'auto-detected' badge for kind=orphan tasks"
```

---

## Task 9: Manual smoke test — end-to-end verification

**Files:** (read-only verification, no edits)

- [ ] **Step 1: Restart uvicorn so the new code loads**

```bash
PID=$(lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | awk '/LISTEN/{print $2}' | head -1)
kill "$PID" 2>/dev/null; sleep 2
source ~/miniconda3/etc/profile.d/conda.sh && conda activate tradingagents
nohup uvicorn app.api:app --host 127.0.0.1 --port 8000 >> logs/backend.log 2>&1 &
sleep 3
curl -s -o /dev/null -w "backend %{http_code}\n" http://127.0.0.1:8000/docs
```
Expected: `backend 200`

- [ ] **Step 2: Double-click test (debounce window)**

In the UI, click **Launch** twice within 5 seconds.
Expected: Second click shows toast: `"A run started X.Xs ago. Please wait Y s before launching another."`
DB confirmation:
```bash
sqlite3 ~/.tradingagents/app/decisions.sqlite3 "SELECT COUNT(*) FROM run_log WHERE run_date=date('now') AND started_at >= datetime('now','-1 minute');"
```
Expected: `1` (only the first launch created a run_log row)

- [ ] **Step 3: Cap test (manual=1 enforcement)**

Wait until the first launch finishes (or stop it from the UI). Click Launch once (succeeds). Wait 16 seconds (past debounce). Click Launch again.
Expected: Second click shows toast: `"manual capacity exhausted (1/1). Wait for a slot or stop an existing run."`

- [ ] **Step 4: Orphan test (registry self-healing)**

While a run is in flight:
```bash
# Kill uvicorn — runner subprocess stays alive (start_new_session=True)
PID=$(lsof -nP -iTCP:8000 -sTCP:LISTEN | awk '/LISTEN/{print $2}' | head -1)
kill "$PID"; sleep 2

# Wipe active_tasks.json so the runner becomes truly orphan
echo '[]' > ~/.tradingagents/app/active_tasks.json

# Restart uvicorn
source ~/miniconda3/etc/profile.d/conda.sh && conda activate tradingagents
nohup uvicorn app.api:app --host 127.0.0.1 --port 8000 >> logs/backend.log 2>&1 &
sleep 3

# Hit the active-runs endpoint
curl -s http://127.0.0.1:8000/api/runs/active | python3 -m json.tool
```
Expected: JSON contains one entry with `"kind": "orphan"`, `"tickers": [...]`, `"deep_model": null`. UI shows an amber "auto-detected" badge.

- [ ] **Step 5: Stop the orphan to clean up**

In the UI, click Stop on the orphan row. Verify the row disappears.
Expected: Within ~5 seconds the row vanishes, and:
```bash
ps aux | grep "app.runner" | grep -v grep
```
returns nothing (no live runner left).

- [ ] **Step 6: Final commit if any cleanup**

If you had to tweak anything to make smoke tests pass:
```bash
git add -p && git commit -m "fix(tasks): <whatever you actually changed>"
```
Otherwise this task has no commit — the verification is the deliverable.
