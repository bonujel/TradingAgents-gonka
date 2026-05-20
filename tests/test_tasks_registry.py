"""Tests for OS-backed task registry helpers in app.tasks."""

from __future__ import annotations

import json

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.tasks import (
    _parse_etime,
    _parse_runner_cmdline,
    _scan_runner_processes,
    list_active,
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
