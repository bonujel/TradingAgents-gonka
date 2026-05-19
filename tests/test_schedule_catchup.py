from __future__ import annotations

from typing import Any

import pytest

from app import api, schedule_store, scheduler_thread, tasks


@pytest.fixture()
def isolated_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "APP_HOME", tmp_path)
    monkeypatch.setattr(schedule_store, "SCHEDULE_PATH", tmp_path / "schedule.json")
    yield


def _save_enabled(**overrides: Any) -> dict[str, Any]:
    cfg = {
        "enabled": True,
        "start_hour": 16,
        "start_minute": 30,
        "interval_hours": 24,
        "workers": 4,
        "tickers": ["NVDA"],
        **overrides,
    }
    schedule_store.save_schedule(cfg)
    return schedule_store.load_schedule()


def test_schedule_defaults_include_catch_up_state(isolated_schedule):
    cfg = schedule_store.load_schedule()

    assert cfg["pending_catch_up"] is False
    assert cfg["missed_count"] == 0
    assert cfg["last_missed_at"] is None
    assert cfg["last_catch_up_at"] is None
    assert cfg["last_regular_fire_at"] is None
    assert cfg["last_start_error"] is None


def test_mark_missed_fire_coalesces_and_consume_clears(isolated_schedule):
    _save_enabled()

    first = schedule_store.mark_missed_fire()
    second = schedule_store.mark_missed_fire()

    assert first["pending_catch_up"] is True
    assert second["pending_catch_up"] is True
    assert second["missed_count"] == 2
    assert second["last_missed_at"]

    consumed = schedule_store.consume_pending_catch_up()
    assert consumed["pending_catch_up"] is False
    assert consumed["missed_count"] == 0
    assert consumed["last_catch_up_at"]


def test_regular_fire_marks_pending_when_scheduled_slot_is_full(
    isolated_schedule, monkeypatch
):
    _save_enabled()

    def raise_capacity(reason: str) -> bool:
        raise tasks.CapacityExceeded("scheduled", 1, 1)

    monkeypatch.setattr(scheduler_thread, "_start_scheduled_run", raise_capacity)

    scheduler_thread._fire_scheduled_run()

    cfg = schedule_store.load_schedule()
    assert cfg["pending_catch_up"] is True
    assert cfg["missed_count"] == 1
    assert cfg["last_missed_at"]
    assert cfg["last_regular_fire_at"]


def test_catch_up_starts_when_slot_is_free_and_clears_pending(
    isolated_schedule, monkeypatch
):
    _save_enabled(pending_catch_up=True, missed_count=3)
    seen: list[str] = []

    monkeypatch.setattr(
        scheduler_thread.tasks,
        "count_active_by_kind",
        lambda: {"manual": 0, "scheduled": 0},
    )

    def start(reason: str) -> bool:
        seen.append(reason)
        return True

    monkeypatch.setattr(scheduler_thread, "_start_scheduled_run", start)

    scheduler_thread._maybe_start_catch_up()

    cfg = schedule_store.load_schedule()
    assert seen == ["catch-up"]
    assert cfg["pending_catch_up"] is False
    assert cfg["missed_count"] == 0
    assert cfg["last_catch_up_at"]


def test_catch_up_keeps_pending_when_start_fails(isolated_schedule, monkeypatch):
    _save_enabled(pending_catch_up=True, missed_count=2)

    monkeypatch.setattr(
        scheduler_thread.tasks,
        "count_active_by_kind",
        lambda: {"manual": 0, "scheduled": 0},
    )

    def fail_start(reason: str) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler_thread, "_start_scheduled_run", fail_start)

    scheduler_thread._maybe_start_catch_up()

    cfg = schedule_store.load_schedule()
    assert cfg["pending_catch_up"] is True
    assert cfg["missed_count"] == 2
    assert cfg["last_start_error"] == "boom"


def test_api_put_schedule_preserves_and_can_clear_pending(isolated_schedule, monkeypatch):
    _save_enabled(pending_catch_up=True, missed_count=4, last_missed_at="2026-05-19T10:00:00")
    monkeypatch.setattr(api.scheduler_thread, "reload_schedule", lambda: {"next_run": None})

    update = api.ScheduleUpdate(
        enabled=True,
        start_hour=9,
        start_minute=15,
        interval_hours=12,
        workers=3,
        tickers=["aapl", "msft"],
    )
    api.put_schedule(update)

    cfg = schedule_store.load_schedule()
    assert cfg["start_hour"] == 9
    assert cfg["tickers"] == ["AAPL", "MSFT"]
    assert cfg["pending_catch_up"] is True
    assert cfg["missed_count"] == 4

    clear = api.ScheduleUpdate(
        enabled=True,
        start_hour=9,
        start_minute=15,
        interval_hours=12,
        workers=3,
        tickers=["AAPL"],
        clear_pending=True,
    )
    api.put_schedule(clear)

    cfg = schedule_store.load_schedule()
    assert cfg["pending_catch_up"] is False
    assert cfg["missed_count"] == 0


def test_api_pause_can_clear_pending_when_configured(isolated_schedule, monkeypatch):
    _save_enabled(
        pending_catch_up=True,
        missed_count=2,
        pause_clears_pending=True,
    )
    monkeypatch.setattr(api.scheduler_thread, "reload_schedule", lambda: {"next_run": None})

    update = api.ScheduleUpdate(
        enabled=False,
        start_hour=16,
        start_minute=30,
        interval_hours=24,
        workers=4,
        tickers=[],
    )

    api.put_schedule(update)

    cfg = schedule_store.load_schedule()
    assert cfg["enabled"] is False
    assert cfg["pending_catch_up"] is False
    assert cfg["missed_count"] == 0
