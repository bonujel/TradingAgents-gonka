"""In-process scheduler driving the single configured run.

A `BackgroundScheduler` lives inside the FastAPI uvicorn worker so the
dashboard owns scheduling and run-launching as one process. The job
respects the per-kind concurrency cap in ``tasks.MAX_PER_KIND`` — a
scheduled fire that lands on top of an already-running scheduled run is
coalesced into one pending catch-up run rather than queued without bound.

The schedule is reloaded whenever the operator PUTs `/api/schedule`, so
toggling enable/disable, changing the start time, or editing the
interval all take effect immediately without restarting uvicorn.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import schedule_store, tasks
from .settings_store import load_settings, mode_is_configured
from .sp500 import get_top_tickers


logger = logging.getLogger(__name__)

_JOB_ID = "tradingagents-scheduled-run"
_CATCH_UP_JOB_ID = "tradingagents-scheduled-catch-up"
_SCHED: Optional[BackgroundScheduler] = None
_LOCK = threading.Lock()


def _get_scheduler() -> BackgroundScheduler:
    global _SCHED
    with _LOCK:
        if _SCHED is None:
            # Anchor the scheduler in UTC. start_hour / start_minute in
            # schedule.json are UTC integers; the frontend converts
            # between the operator's tz and UTC at the form layer.
            _SCHED = BackgroundScheduler(timezone=timezone.utc)
            _SCHED.start()
            _SCHED.add_job(
                _maybe_start_catch_up,
                trigger=IntervalTrigger(seconds=60),
                id=_CATCH_UP_JOB_ID,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        return _SCHED


def shutdown() -> None:
    """Stop the scheduler on uvicorn shutdown."""
    global _SCHED
    with _LOCK:
        if _SCHED is not None:
            _SCHED.shutdown(wait=False)
            _SCHED = None


def _start_scheduled_run(reason: Literal["regular", "catch-up"]) -> bool:
    """Start one scheduled run, returning whether a child was launched.

    CapacityExceeded intentionally bubbles so regular fires can mark a
    pending catch-up while catch-up polling can simply try again later.
    """
    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled"):
        logger.info("Scheduled %s skipped — schedule is disabled", reason)
        return False

    settings = load_settings()
    if not mode_is_configured(settings):
        schedule_store.mark_start_error("missing credentials")
        logger.warning(
            "Scheduled %s skipped — connection mode '%s' missing credentials",
            reason, settings.get("mode"),
        )
        return False

    tickers = [t.strip().upper() for t in (cfg.get("tickers") or []) if t.strip()]
    if not tickers:
        tickers = get_top_tickers(20)
    workers = max(1, int(cfg.get("workers", 4)))

    task = tasks.start_run(
        settings=settings,
        tickers=tickers,
        workers=workers,
        kind="scheduled",
    )
    logger.info(
        "Scheduled %s run started: PID %d, %d tickers, workers=%d",
        reason, task["pid"], len(tickers), workers,
    )
    return True


def _fire_scheduled_run() -> None:
    schedule_store.mark_regular_fire()
    try:
        _start_scheduled_run("regular")
    except tasks.CapacityExceeded as exc:
        cfg = schedule_store.mark_missed_fire()
        logger.warning(
            "Scheduled fire deferred — %s; missed_count=%s",
            exc, cfg.get("missed_count"),
        )
    except Exception as exc:
        schedule_store.mark_start_error(str(exc))
        logger.exception("Scheduled fire failed")


def _maybe_start_catch_up() -> None:
    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled") or not cfg.get("pending_catch_up"):
        return

    counts = tasks.count_active_by_kind()
    if counts.get("scheduled", 0) > 0:
        return

    try:
        started = _start_scheduled_run("catch-up")
    except tasks.CapacityExceeded:
        return
    except Exception as exc:
        schedule_store.mark_start_error(str(exc))
        logger.exception("Scheduled catch-up failed")
        return

    if started:
        schedule_store.consume_pending_catch_up()


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

    # Compute the first fire as "today at HH:MM UTC". start_hour /
    # start_minute are UTC integers (the frontend submits in UTC after
    # converting from the operator's local tz). If we've already passed
    # that time, advance by ``interval_hours`` until the next slot is in
    # the future. APScheduler then takes over with the interval trigger;
    # using ``start_date`` (vs CronTrigger) keeps the spacing exact even
    # when interval_hours doesn't divide 24.
    now = datetime.now(timezone.utc)
    first = now.replace(
        hour=int(cfg["start_hour"]),
        minute=int(cfg["start_minute"]),
        second=0,
        microsecond=0,
    )
    interval = max(1, int(cfg.get("interval_hours", 24)))
    while first <= now:
        first += timedelta(hours=interval)

    trigger = IntervalTrigger(hours=interval, start_date=first, timezone=timezone.utc)
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
