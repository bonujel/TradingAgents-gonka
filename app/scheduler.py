"""APScheduler-driven daily trigger for the runner.

Default schedule: every weekday at 16:30 US/Eastern, i.e. ~30 min after the
US cash session closes so yfinance has settled end-of-day prices. Override
via env vars:

    TRADINGAGENTS_APP_CRON_HOUR=16
    TRADINGAGENTS_APP_CRON_MINUTE=30
    TRADINGAGENTS_APP_TIMEZONE=America/New_York
    TRADINGAGENTS_APP_TOP_N=20
"""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .runner import run_daily


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — falling back to %d", name, raw, default)
        return default


def _scheduled_job() -> None:
    top_n = _env_int("TRADINGAGENTS_APP_TOP_N", 20)
    logger.info("Scheduled run starting (top_n=%d)", top_n)
    summary = run_daily(top_n=top_n)
    logger.info("Scheduled run finished: %s", summary)


def build_scheduler() -> BackgroundScheduler:
    hour = _env_int("TRADINGAGENTS_APP_CRON_HOUR", 16)
    minute = _env_int("TRADINGAGENTS_APP_CRON_MINUTE", 30)
    timezone = os.environ.get("TRADINGAGENTS_APP_TIMEZONE", "America/New_York")
    day_of_week = os.environ.get("TRADINGAGENTS_APP_CRON_DOW", "mon-fri")

    scheduler = BackgroundScheduler(timezone=timezone)
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone=timezone),
        id="trading_agents_daily",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    logger.info(
        "Scheduled daily run: %s %02d:%02d (%s)",
        day_of_week, hour, minute, timezone,
    )
    return scheduler


def start(block: bool = True, run_now: Optional[bool] = None) -> BackgroundScheduler:
    """Start the scheduler. When ``run_now`` is True, kick off an immediate run.

    ``run_now`` defaults to the ``TRADINGAGENTS_APP_RUN_ON_START`` env var
    (truthy values: ``1``, ``true``, ``yes``). Useful for a freshly deployed
    container where you want one decision row in the dashboard before waiting
    for the next cron tick.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    if run_now is None:
        run_now = os.environ.get("TRADINGAGENTS_APP_RUN_ON_START", "").lower() in ("1", "true", "yes", "on")

    scheduler = build_scheduler()
    scheduler.start()

    if run_now:
        logger.info("Running immediately because run_now=True")
        _scheduled_job()

    if not block:
        return scheduler

    stop_event = {"stop": False}

    def _shutdown(signum, frame):  # noqa: ARG001
        logger.info("Signal %s received — shutting down", signum)
        stop_event["stop"] = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not stop_event["stop"]:
            time.sleep(1)
    finally:
        scheduler.shutdown(wait=False)
    return scheduler


if __name__ == "__main__":
    start()
