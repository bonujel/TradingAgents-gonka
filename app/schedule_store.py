"""Persist the operator-configured schedule.

The system supports **one** schedule entry. The same `~/.tradingagents/app/`
directory is shared with `settings_store`. The on-disk schema lives in
`schedule.json`; we keep the file even when ``enabled=false`` so the
operator's time/interval choices survive a disable/enable cycle.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .settings_store import APP_HOME

SCHEDULE_PATH = APP_HOME / "schedule.json"


def _defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "start_hour": 16,
        "start_minute": 30,
        "interval_hours": 24,
        "workers": 4,
        "tickers": [],
    }


def load_schedule() -> dict[str, Any]:
    if not SCHEDULE_PATH.exists():
        return _defaults()
    try:
        stored = json.loads(SCHEDULE_PATH.read_text())
        return {**_defaults(), **stored}
    except json.JSONDecodeError:
        return _defaults()


def save_schedule(cfg: dict[str, Any]) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(SCHEDULE_PATH)


def server_time_info() -> dict[str, Any]:
    """Return the FastAPI host's local clock + tz info for the UI."""
    now = datetime.now().astimezone()
    offset = now.utcoffset()
    return {
        "now": now.isoformat(timespec="seconds"),
        "tz_name": now.tzname() or time.tzname[0] or "local",
        "tz_offset_seconds": int(offset.total_seconds()) if offset else 0,
    }
