"""Tests for OS-backed task registry helpers in app.tasks."""

from __future__ import annotations

import json

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.tasks import (
    MAX_PER_KIND,
    RecentDuplicateLaunch,
    _check_recent_launch,
    _dedup_window_seconds,
    _parse_etime,
    _parse_runner_cmdline,
    _scan_runner_processes,
    list_active,
    read_log_tail,
)


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


def test_parse_etime_returns_zero_when_days_overflows_timedelta():
    """``timedelta`` rejects |days| > 999999999. The caller must never see
    that ``OverflowError`` — a single nonsense ``ps`` row should degrade to
    ``timedelta(0)``, same as any other unparseable input."""
    assert _parse_etime("99999999999-00:00:00") == timedelta(0)


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
    fixed_now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.tasks._run_ps_scan", return_value=fake), \
         patch("app.tasks._utc_now", return_value=fixed_now):
        rows = _scan_runner_processes()
    assert rows[0]["started_at"] == "2026-05-20T11:59:00+00:00"


def test_scan_runner_processes_returns_empty_on_ps_failure():
    with patch("app.tasks._run_ps_scan", return_value=None):
        assert _scan_runner_processes() == []


def test_scan_runner_processes_isolates_overflowing_row():
    """A single ``ps`` row whose etime makes ``now - delta`` underflow
    ``datetime.min`` must not poison the whole scan. The bad row is
    dropped; the good row is still returned.

    The 999999998-day etime is within ``timedelta``'s 10^9-day ceiling
    (so ``_parse_etime`` returns it as-is) but far enough back to push
    a 2026-era ``now`` below year 1 on subtraction.
    """
    fake = _fake_ps_output([
        ("100", "00:05", "S", "/usr/bin/python -m app.runner -j 8 NVDA"),
        ("999", "999999998-00:00:00", "S", "/usr/bin/python -m app.runner MSFT"),
    ])
    fixed_now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.tasks._run_ps_scan", return_value=fake), \
         patch("app.tasks._utc_now", return_value=fixed_now):
        rows = _scan_runner_processes()
    pids = [r["pid"] for r in rows]
    assert 100 in pids
    assert 999 not in pids


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


def test_max_per_kind_caps_manual_at_one():
    assert MAX_PER_KIND["manual"] == 1


def test_max_per_kind_caps_scheduled_at_one():
    assert MAX_PER_KIND["scheduled"] == 1


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
    fixed_now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)  # 5s after launch
    with patch("app.tasks._utc_now", return_value=fixed_now):
        with pytest.raises(RecentDuplicateLaunch) as exc:
            _check_recent_launch(active, window_seconds=15)
    assert 4.5 < exc.value.gap_seconds < 5.5


def test_check_recent_launch_allows_when_outside_window():
    active = [{
        "pid": 1, "started_at": "2026-05-20T11:00:00", "kind": "manual",
        "tickers": [], "workers": 1, "log_path": None,
        "mode": None, "deep_model": None,
    }]
    fixed_now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)  # 1 hour after
    with patch("app.tasks._utc_now", return_value=fixed_now):
        _check_recent_launch(active, window_seconds=15)  # no raise


def test_check_recent_launch_no_active_no_raise():
    fixed_now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.tasks._utc_now", return_value=fixed_now):
        _check_recent_launch([], window_seconds=15)


def test_dedup_window_seconds_default():
    assert _dedup_window_seconds() == 15


def test_dedup_window_seconds_env_override(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DEDUP_WINDOW_SECONDS", "30")
    assert _dedup_window_seconds() == 30


def test_read_log_tail_returns_empty_for_none_path():
    """``list_active`` flags orphan-runner entries with ``log_path=None``.
    The log-tail endpoint must not propagate that into ``open(None)``."""
    assert read_log_tail(None) == ""


def test_read_log_tail_returns_empty_for_missing_file(tmp_path):
    assert read_log_tail(str(tmp_path / "nonexistent.log")) == ""


def test_api_returns_409_on_recent_duplicate(monkeypatch):
    """Hitting POST /api/runs when a recent launch exists → 409."""
    from fastapi.testclient import TestClient

    from app.api import app
    from app import auth as auth_mod
    from app import tasks as tasks_mod
    from app import api as api_mod

    # Every /api/ route sits behind the bearer-token dependency, and
    # POST /api/runs additionally gates on the super-admin role since
    # commit 6287a67. Override both so this test can exercise the
    # run-launch logic without minting a token; monkeypatch.setitem
    # restores each entry afterwards.
    monkeypatch.setitem(
        app.dependency_overrides, auth_mod.require_authenticated, lambda: None
    )
    monkeypatch.setitem(
        app.dependency_overrides, auth_mod.require_admin, lambda: None
    )

    monkeypatch.setattr(
        api_mod,
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
    resp = client.post(
        "/api/runs",
        json={"tickers": ["NVDA"], "workers": 4, "kind": "manual"},
    )
    assert resp.status_code == 409
    assert "3.0s ago" in resp.json()["detail"]
