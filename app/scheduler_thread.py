"""In-process scheduler driving the single configured run.

A `BackgroundScheduler` lives inside the FastAPI uvicorn worker so the
dashboard owns scheduling and run-launching as one process. The job
respects the per-kind concurrency cap in ``tasks.MAX_PER_KIND`` — a
scheduled fire that lands on top of an already-running scheduled run is
logged and skipped rather than queued.

The schedule is reloaded whenever the operator PUTs `/api/schedule`, so
toggling enable/disable, changing the start time, or editing the
interval all take effect immediately without restarting uvicorn.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import schedule_store, tasks
from .settings_store import load_settings, mode_is_configured
from .sp500 import get_top_tickers


logger = logging.getLogger(__name__)

_JOB_ID = "tradingagents-scheduled-run"
_SCHED: Optional[BackgroundScheduler] = None
_LOCK = threading.Lock()


def _get_scheduler() -> BackgroundScheduler:
    global _SCHED
    with _LOCK:
        if _SCHED is None:
            _SCHED = BackgroundScheduler()
            _SCHED.start()
        return _SCHED


def shutdown() -> None:
    """Stop the scheduler on uvicorn shutdown."""
    global _SCHED
    with _LOCK:
        if _SCHED is not None:
            _SCHED.shutdown(wait=False)
            _SCHED = None


def _fire_scheduled_run() -> None:
    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled"):
        logger.info("Scheduled fire skipped — schedule is disabled")
        return

    settings = load_settings()
    if not mode_is_configured(settings):
        logger.warning(
            "Scheduled fire skipped — connection mode '%s' missing credentials",
            settings.get("mode"),
        )
        return

    tickers = [t.strip().upper() for t in (cfg.get("tickers") or []) if t.strip()]
    if not tickers:
        tickers = get_top_tickers(20)
    workers = max(1, int(cfg.get("workers", 4)))

    try:
        task = tasks.start_run(
            settings=settings,
            tickers=tickers,
            workers=workers,
            kind="scheduled",
        )
        logger.info(
            "Scheduled run started: PID %d, %d tickers, workers=%d",
            task["pid"], len(tickers), workers,
        )
    except tasks.CapacityExceeded as exc:
        logger.warning("Scheduled fire skipped — %s", exc)
    except Exception:
        logger.exception("Scheduled fire failed")


def reload_schedule() -> dict[str, Any]:
    """Drop any existing job, then re-register from the on-disk config."""
    sched = _get_scheduler()
    try:
        sched.remove_job(_JOB_ID)
    except Exception:
        pass

    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled"):
        return {"next_run": None}

    # Compute the first fire as "today at HH:MM in local tz". If we've
    # already passed that time, advance by ``interval_hours`` until the
    # next slot is in the future. APScheduler then takes over with the
    # interval trigger; using ``start_date`` (vs CronTrigger) keeps the
    # spacing exact even when interval_hours doesn't divide 24.
    now = datetime.now().astimezone()
    first = now.replace(
        hour=int(cfg["start_hour"]),
        minute=int(cfg["start_minute"]),
        second=0,
        microsecond=0,
    )
    interval = max(1, int(cfg.get("interval_hours", 24)))
    while first <= now:
        first += timedelta(hours=interval)

    trigger = IntervalTrigger(hours=interval, start_date=first)
    sched.add_job(
        _fire_scheduled_run,
        trigger=trigger,
        id=_JOB_ID,
        replace_existing=True,
        misfire_grace_time=60 * 5,
        coalesce=True,
        max_instances=1,
    )
    job = sched.get_job(_JOB_ID)
    return {
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
    }


def get_status() -> dict[str, Any]:
    sched = _get_scheduler()
    job = sched.get_job(_JOB_ID)
    return {
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "running": _SCHED is not None and _SCHED.running,
    }
