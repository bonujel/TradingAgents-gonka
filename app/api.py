"""FastAPI backend for the Nuxt operator dashboard.

Run::

    uvicorn app.api:app --reload --port 8000

The API is process-local in the sense that the subprocess registry lives
inside the worker that handled the launch. Run with a single worker
(``--workers 1`` is the default) so Stop requests always reach the
process that owns the ``Popen`` handle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import auth, db, schedule_store, scheduler_thread, tasks
from .settings_store import (
    MODEL_OPTIONS,
    load_settings,
    mode_is_configured,
    public_view,
    save_settings,
)
from .sp500 import get_sp100_tickers, get_sp500_tickers, get_top_tickers


logger = logging.getLogger(__name__)


app = FastAPI(
    title="TradingAgents · Gonka API",
    version="1.0.0",
    description="Backend for the Nuxt operator dashboard.",
    # App-level dependency: every /api/ route except the public ones
    # (login, health) requires a valid bearer token. Raising HTTPException
    # here — rather than in a middleware — keeps CORS headers on the 401,
    # so the browser can actually read the response.
    dependencies=[Depends(auth.require_authenticated)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _bootstrap() -> None:
    db.init_db()
    auth.init_auth()
    scheduler_thread.reload_schedule()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler_thread.shutdown()


# ─── Schemas ──────────────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    mode: str = Field(pattern="^(router|sdk)$")
    router_api_key: Optional[str] = None
    sdk_private_key: Optional[str] = None
    sdk_source_url: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    max_workers: int = Field(default=4, ge=1, le=16)
    qwen_max_tokens: int = Field(default=4096, ge=256, le=32768)
    kimi_max_tokens: int = Field(default=8192, ge=256, le=32768)
    llm_debug: bool = False
    disable_kimi_thinking: bool = False


class RunRequest(BaseModel):
    tickers: list[str] = Field(min_length=1)
    workers: int = Field(default=4, ge=1, le=16)
    kind: str = Field(default="manual", pattern="^(manual|scheduled)$")


class ScheduleUpdate(BaseModel):
    enabled: bool
    start_hour: int = Field(ge=0, le=23)
    start_minute: int = Field(ge=0, le=59)
    interval_hours: int = Field(ge=1, le=168)
    workers: int = Field(ge=1, le=16)
    tickers: list[str] = Field(default_factory=list)
    pause_clears_pending: Optional[bool] = None
    clear_pending: bool = False


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


# ─── Info / settings ──────────────────────────────────────────────────────


@app.get("/api/info")
def get_info() -> dict[str, Any]:
    settings = load_settings()
    counts = tasks.count_active_by_kind()
    return {
        "mode": settings["mode"],
        "deep_model": settings.get("deep_model"),
        "quick_model": settings.get("quick_model"),
        "max_workers": settings.get("max_workers", 4),
        "configured": mode_is_configured(settings),
        "models": list(MODEL_OPTIONS),
        "db_path": str(db.get_db_path()),
        "capacity": {
            kind: {
                "current": counts.get(kind, 0),
                "max": tasks.MAX_PER_KIND[kind],
            }
            for kind in tasks.MAX_PER_KIND
        },
    }


@app.get("/api/settings", dependencies=[Depends(auth.require_admin)])
def get_settings() -> dict[str, Any]:
    return public_view(load_settings())


@app.put("/api/settings", dependencies=[Depends(auth.require_admin)])
def put_settings(update: SettingsUpdate) -> dict[str, Any]:
    """Update settings.

    Empty-string credential fields are treated as "leave the stored value
    alone" so the user can edit non-secret fields without re-entering the
    private key on every save. Sending an explicit ``null`` would clear,
    but the frontend uses empty strings for unchanged inputs.
    """
    current = load_settings()
    next_settings = {**current}
    next_settings["mode"] = update.mode
    if update.router_api_key:
        next_settings["router_api_key"] = update.router_api_key
    if update.sdk_private_key:
        next_settings["sdk_private_key"] = update.sdk_private_key
    if update.sdk_source_url is not None:
        next_settings["sdk_source_url"] = update.sdk_source_url
    if update.deep_model:
        next_settings["deep_model"] = update.deep_model
    if update.quick_model:
        next_settings["quick_model"] = update.quick_model
    next_settings["max_workers"] = update.max_workers
    next_settings["qwen_max_tokens"] = update.qwen_max_tokens
    next_settings["kimi_max_tokens"] = update.kimi_max_tokens
    next_settings["llm_debug"] = update.llm_debug
    next_settings["disable_kimi_thinking"] = update.disable_kimi_thinking
    save_settings(next_settings)
    return public_view(next_settings)


# ─── Decisions ────────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@app.get("/api/decisions/dates")
def list_dates() -> list[str]:
    return db.list_run_dates()


@app.get("/api/decisions")
def list_decisions(
    date: str = Query(...),
    ticker: Optional[str] = Query(default=None),
    rating: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = db.list_decisions_summary(
        trade_date=date,
        ticker=ticker,
        rating=rating,
        deep_model=model,
        page=page,
        page_size=page_size,
    )
    return {
        "rows": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/decisions/models")
def list_decision_models(date: str = Query(...)) -> list[str]:
    return db.list_distinct_models(trade_date=date)


@app.get("/api/decisions/{ticker}/{trade_date}")
def get_decision(ticker: str, trade_date: str) -> dict[str, Any]:
    rows = db.list_decisions(trade_date=trade_date, ticker=ticker, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Decision not found")
    return _row_to_dict(rows[0])


# ─── Runs ─────────────────────────────────────────────────────────────────


@app.get("/api/runs/active", dependencies=[Depends(auth.require_admin)])
def list_active_runs() -> list[dict[str, Any]]:
    return [tasks.task_with_runtime(t) for t in tasks.list_active()]


@app.post("/api/runs", status_code=201, dependencies=[Depends(auth.require_admin)])
def start_run(req: RunRequest) -> dict[str, Any]:
    settings = load_settings()
    if not mode_is_configured(settings):
        raise HTTPException(
            status_code=400,
            detail=f"Mode '{settings['mode']}' is missing credentials. Update settings first.",
        )
    cleaned = [t.strip().upper() for t in req.tickers if t.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="No tickers specified.")
    try:
        task = tasks.start_run(
            settings=settings,
            tickers=cleaned,
            workers=req.workers,
            kind=req.kind,
        )
    except tasks.RecentDuplicateLaunch as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except tasks.CapacityExceeded as exc:
        # 409 Conflict — the request was valid but the system is at the
        # per-kind cap. Frontend uses this to disable the Run button.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{exc.kind} capacity exhausted ({exc.current}/{exc.limit}). "
                "Wait for a slot or stop an existing run."
            ),
        )
    return tasks.task_with_runtime(task)


@app.delete("/api/runs/{pid}", dependencies=[Depends(auth.require_admin)])
def stop_run(pid: int) -> dict[str, Any]:
    ok = tasks.stop_run(pid)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already exited.")
    return {"pid": pid, "stopped": True}


@app.get("/api/runs/{pid}/log", dependencies=[Depends(auth.require_admin)])
def read_log(pid: int, lines: int = Query(default=30, ge=1, le=2000)) -> dict[str, Any]:
    """Return the tail of a run's log file by its PID."""
    for task in tasks.list_active():
        if task["pid"] == pid:
            return {"pid": pid, "log": tasks.read_log_tail(task["log_path"], lines)}
    raise HTTPException(status_code=404, detail="Active task not found.")


@app.get("/api/runs/log_by_path", dependencies=[Depends(auth.require_admin)])
def read_log_by_path(path: str, lines: int = Query(default=30, ge=1, le=2000)) -> dict[str, Any]:
    """Fallback log-reader for tasks whose entry has already been pruned."""
    requested = Path(path).resolve()
    log_root = (tasks._LOG_DIR).resolve()
    if not str(requested).startswith(str(log_root)):
        raise HTTPException(status_code=400, detail="Path outside log directory.")
    return {"path": str(requested), "log": tasks.read_log_tail(str(requested), lines)}


@app.get("/api/runs/recent", dependencies=[Depends(auth.require_admin)])
def list_recent_runs(limit: int = Query(default=10, ge=1, le=200)) -> list[dict[str, Any]]:
    rows = db.list_runs(limit=limit)
    out: list[dict[str, Any]] = []
    for row in rows:
        d = _row_to_dict(row)
        elapsed = None
        if d.get("finished_at") and d.get("started_at"):
            try:
                t0 = datetime.fromisoformat(d["started_at"])
                t1 = datetime.fromisoformat(d["finished_at"])
                elapsed = int((t1 - t0).total_seconds())
            except ValueError:
                pass
        d["elapsed_seconds"] = elapsed
        try:
            d["tickers"] = json.loads(d.get("tickers") or "[]")
        except (TypeError, json.JSONDecodeError):
            d["tickers"] = []
        out.append(d)
    return out


# ─── Tickers ──────────────────────────────────────────────────────────────


@app.get("/api/tickers/top", dependencies=[Depends(auth.require_admin)])
def tickers_top(n: int = Query(default=20, ge=1, le=500)) -> list[str]:
    return get_top_tickers(n)


@app.get("/api/tickers/sp100", dependencies=[Depends(auth.require_admin)])
def tickers_sp100() -> list[str]:
    return get_sp100_tickers()


@app.get("/api/tickers/sp500", dependencies=[Depends(auth.require_admin)])
def tickers_full() -> list[str]:
    return get_sp500_tickers()


# ─── Health ───────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ─── Schedule ─────────────────────────────────────────────────────────────


def _schedule_payload() -> dict[str, Any]:
    cfg = schedule_store.load_schedule()
    status = scheduler_thread.get_status()
    counts = tasks.count_active_by_kind()
    return {
        "config": cfg,
        "next_run": status.get("next_run"),
        "scheduler_running": status.get("running", False),
        "server_time": schedule_store.server_time_info(),
        "capacity": {
            kind: {
                "current": counts.get(kind, 0),
                "max": tasks.MAX_PER_KIND[kind],
            }
            for kind in tasks.MAX_PER_KIND
        },
    }


@app.get("/api/schedule", dependencies=[Depends(auth.require_admin)])
def get_schedule() -> dict[str, Any]:
    return _schedule_payload()


@app.put("/api/schedule", dependencies=[Depends(auth.require_admin)])
def put_schedule(body: ScheduleUpdate) -> dict[str, Any]:
    cleaned_tickers = [t.strip().upper() for t in body.tickers if t.strip()]
    current = schedule_store.load_schedule()
    incoming = body.model_dump()
    incoming.pop("clear_pending", None)
    pause_clears_pending = incoming.pop("pause_clears_pending")

    cfg = {
        **current,
        **incoming,
        "tickers": cleaned_tickers,
    }
    if pause_clears_pending is not None:
        cfg["pause_clears_pending"] = pause_clears_pending
    if body.clear_pending or (not cfg["enabled"] and cfg.get("pause_clears_pending")):
        cfg["pending_catch_up"] = False
        cfg["missed_count"] = 0
    schedule_store.save_schedule(cfg)
    scheduler_thread.reload_schedule()
    return _schedule_payload()


# ─── Authentication ───────────────────────────────────────────────────────


@app.post("/api/auth/login")
def login(body: LoginRequest) -> dict[str, Any]:
    role = auth.verify_credentials(body.username, body.password)
    if not role:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    username = body.username.strip()
    if username != auth.SA_USERNAME:
        username = username.lower()
    token = auth.make_token(username, role)
    return {"token": token, "username": username, "role": role}


@app.get("/api/auth/me")
def whoami(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    return {"username": user["u"], "role": user["r"]}


@app.post("/api/auth/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: dict = Depends(auth.current_user),
) -> dict[str, Any]:
    username = user["u"]
    if username == auth.SA_USERNAME:
        raise HTTPException(
            status_code=400,
            detail="The super-admin password rotates on restart and cannot be changed here.",
        )
    if not auth.verify_credentials(username, body.old_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    try:
        auth.set_password(username, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ─── User management (super admin only) ───────────────────────────────────


@app.get("/api/users", dependencies=[Depends(auth.require_admin)])
def list_users() -> list[dict[str, Any]]:
    return auth.list_users()


@app.post("/api/users", status_code=201, dependencies=[Depends(auth.require_admin)])
def create_user(body: CreateUserRequest) -> dict[str, Any]:
    try:
        password = auth.create_user(body.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": body.email.strip().lower(), "password": password}


@app.post(
    "/api/users/{username}/reset-password",
    dependencies=[Depends(auth.require_admin)],
)
def reset_user_password(username: str) -> dict[str, Any]:
    try:
        password = auth.reset_password(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": username.strip().lower(), "password": password}


@app.delete("/api/users/{username}", dependencies=[Depends(auth.require_admin)])
def remove_user(username: str) -> dict[str, Any]:
    try:
        auth.delete_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": username.strip().lower(), "deleted": True}
