"""Tests for OS-backed task registry helpers in app.tasks."""

from __future__ import annotations

from datetime import timedelta

from app.tasks import _parse_etime, _parse_runner_cmdline


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
