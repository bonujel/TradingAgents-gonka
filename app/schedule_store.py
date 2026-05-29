"""Persist the operator-configured schedule.

The system supports **one** schedule entry. The same `~/.tradingagents/app/`
directory is shared with `settings_store`. The on-disk schema lives in
`schedule.json`; we keep the file even when ``enabled=false`` so the
operator's time/interval choices survive a disable/enable cycle.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from .settings_store import APP_HOME

SCHEDULE_PATH = APP_HOME / "schedule.json"
_LOCK = threading.RLock()


def _now_iso() -> str:
    """Tz-aware UTC ISO ('...+00:00'). Frontend converts to user-local."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "start_hour": 16,
        "start_minute": 30,
        "interval_hours": 24,
        "workers": 4,
        "tickers": [],
        "pending_catch_up": False,
        "missed_count": 0,
        "last_missed_at": None,
        "last_catch_up_at": None,
        "last_regular_fire_at": None,
        "last_start_error": None,
        "pause_clears_pending": False,
    }


def load_schedule() -> dict[str, Any]:
    with _LOCK:
        if not SCHEDULE_PATH.exists():
            return _defaults()
        try:
            stored = json.loads(SCHEDULE_PATH.read_text())
            return {**_defaults(), **stored}
        except json.JSONDecodeError:
            return _defaults()


def save_schedule(cfg: dict[str, Any]) -> None:
    with _LOCK:
        APP_HOME.mkdir(parents=True, exist_ok=True)
        tmp = SCHEDULE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(SCHEDULE_PATH)


def update_schedule(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Atomically load, mutate, persist, and return the schedule config."""
    with _LOCK:
        cfg = load_schedule()
        mutator(cfg)
        save_schedule(cfg)
        return cfg


def mark_regular_fire() -> dict[str, Any]:
    def mutate(cfg: dict[str, Any]) -> None:
        cfg["last_regular_fire_at"] = _now_iso()
        cfg["last_start_error"] = None

    return update_schedule(mutate)


def mark_missed_fire() -> dict[str, Any]:
    def mutate(cfg: dict[str, Any]) -> None:
        cfg["pending_catch_up"] = True
        cfg["missed_count"] = int(cfg.get("missed_count") or 0) + 1
        cfg["last_missed_at"] = _now_iso()
        cfg["last_start_error"] = None

    return update_schedule(mutate)


def consume_pending_catch_up() -> dict[str, Any]:
    def mutate(cfg: dict[str, Any]) -> None:
        cfg["pending_catch_up"] = False
        cfg["missed_count"] = 0
        cfg["last_catch_up_at"] = _now_iso()
        cfg["last_start_error"] = None

    return update_schedule(mutate)


def clear_pending_catch_up() -> dict[str, Any]:
    def mutate(cfg: dict[str, Any]) -> None:
        cfg["pending_catch_up"] = False
        cfg["missed_count"] = 0

    return update_schedule(mutate)


def mark_start_error(message: str) -> dict[str, Any]:
    def mutate(cfg: dict[str, Any]) -> None:
        cfg["last_start_error"] = message

    return update_schedule(mutate)


def server_time_info() -> dict[str, Any]:
    """Return the FastAPI host's UTC clock for the UI.

    Frontend treats all stored timestamps as UTC and converts them to the
    operator's tz client-side. Server tz is intentionally not exposed —
    the system has one canonical clock (UTC) and the UI is per-operator.
    """
    now = datetime.now(timezone.utc)
    return {
        "now": now.isoformat(timespec="seconds"),
        "tz_name": "UTC",
        "tz_offset_seconds": 0,
    }
