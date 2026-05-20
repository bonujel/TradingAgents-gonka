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
