"""Streamlit dashboard for TradingAgents daily decisions.

Three pages, dispatched by ``st.navigation``:

* **Decisions** — browse stored decisions by trade date and ticker.
* **Tasks** — configure tickers, launch analysis runs as detached subprocesses,
  view active runs (with log tail + Stop button) and recent run history.
* **Settings** — switch between Router and SDK connection modes, set the
  matching credentials, pick the model, persist to ``~/.tradingagents/app/settings.json``.

Launch::

    streamlit run app/dashboard.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow ``streamlit run app/dashboard.py`` from the repo root without an
# editable install — Streamlit only puts the dashboard file's directory on
# sys.path by default, which would hide the ``tradingagents`` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st  # noqa: E402

from app import db  # noqa: E402
from app.sp500 import get_top_tickers  # noqa: E402


# ─── Paths and constants ────────────────────────────────────────────────────

_APP_HOME = Path.home() / ".tradingagents" / "app"
_SETTINGS_PATH = _APP_HOME / "settings.json"
_ACTIVE_TASKS_PATH = _APP_HOME / "active_tasks.json"
_LOG_DIR = _APP_HOME / "logs"

_QWEN_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
_KIMI_MODEL = "moonshotai/Kimi-K2.6"
_MODEL_OPTIONS = [_QWEN_MODEL, _KIMI_MODEL]


# ─── Settings persistence ───────────────────────────────────────────────────

def _settings_defaults_from_env() -> dict:
    """Bootstrap settings from the process env on first load.

    Lets existing users who already have a ``.env`` see their config in the UI
    on first launch, without having to re-enter credentials.
    """
    return {
        "mode": "router" if os.environ.get("GONKA_API_KEY") else "sdk",
        "router_api_key": os.environ.get("GONKA_API_KEY", ""),
        "sdk_private_key": os.environ.get("GONKA_PRIVATE_KEY", ""),
        "sdk_source_url": os.environ.get("GONKA_SOURCE_URL", "https://node4.gonka.ai"),
        "deep_model": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", _QWEN_MODEL),
        "quick_model": os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", _QWEN_MODEL),
        "max_workers": int(os.environ.get("TRADINGAGENTS_APP_MAX_WORKERS", "4")),
    }


def _load_settings() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            stored = json.loads(_SETTINGS_PATH.read_text())
            # Merge with env defaults so new keys added later don't break old files.
            return {**_settings_defaults_from_env(), **stored}
        except json.JSONDecodeError:
            pass
    return _settings_defaults_from_env()


def _save_settings(settings: dict) -> None:
    _APP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(_SETTINGS_PATH)


def _settings_to_env(settings: dict) -> dict:
    """Translate the UI settings into env vars for the runner subprocess.

    Clears inherited Gonka vars first so the chosen mode is unambiguous —
    setting both ``GONKA_API_KEY`` and ``GONKA_PRIVATE_KEY`` would let the
    client pick the SDK path silently and confuse the operator.
    """
    env = os.environ.copy()
    for key in ("GONKA_API_KEY", "GONKA_PRIVATE_KEY", "GONKA_SOURCE_URL"):
        env.pop(key, None)
    if settings["mode"] == "router" and settings.get("router_api_key"):
        env["GONKA_API_KEY"] = settings["router_api_key"]
    elif settings["mode"] == "sdk":
        if settings.get("sdk_private_key"):
            env["GONKA_PRIVATE_KEY"] = settings["sdk_private_key"]
        if settings.get("sdk_source_url"):
            env["GONKA_SOURCE_URL"] = settings["sdk_source_url"]
    env["TRADINGAGENTS_LLM_PROVIDER"] = "gonka"
    env["TRADINGAGENTS_DEEP_THINK_LLM"] = settings.get("deep_model") or _QWEN_MODEL
    env["TRADINGAGENTS_QUICK_THINK_LLM"] = settings.get("quick_model") or _QWEN_MODEL
    env["TRADINGAGENTS_APP_MAX_WORKERS"] = str(settings.get("max_workers", 4))
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _mode_is_configured(settings: dict) -> bool:
    if settings["mode"] == "router":
        return bool(settings.get("router_api_key"))
    return bool(settings.get("sdk_private_key")) and bool(settings.get("sdk_source_url"))


# ─── Task tracking ──────────────────────────────────────────────────────────

def _load_active_tasks() -> list:
    if not _ACTIVE_TASKS_PATH.exists():
        return []
    try:
        return json.loads(_ACTIVE_TASKS_PATH.read_text())
    except json.JSONDecodeError:
        return []


def _save_active_tasks(tasks: list) -> None:
    _APP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = _ACTIVE_TASKS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tasks, indent=2))
    tmp.replace(_ACTIVE_TASKS_PATH)


# Module-level Popen registry. Streamlit re-imports the script on every
# interaction but the module object is cached, so this dict survives reruns
# within a single ``streamlit run`` session. We keep handles here so we can
# call ``.poll()`` and properly reap exited children — otherwise a finished
# runner stays as a zombie in the kernel process table and ``os.kill(pid, 0)``
# misreports it as still alive, leaving the task stuck on "running" forever.
_PROC_HANDLES: dict[int, subprocess.Popen] = {}


def _is_alive(pid: int) -> bool:
    """Return True when the runner subprocess is still executing.

    Two-pronged check:

    1. If we have the ``Popen`` handle (this dashboard session spawned it),
       ``poll()`` is authoritative — it returns ``None`` for running,
       an int exit code once the child terminates (and reaps the zombie).
    2. Otherwise (dashboard restarted while a run was in flight, leaving
       only ``active_tasks.json`` behind), fall back to ``/proc/<pid>/status``
       on Linux: a missing entry means the PID is gone; ``State: Z*`` means
       it's a zombie owned by a now-defunct parent and should be treated
       as dead even though ``kill(0)`` still succeeds against it.
    """
    proc = _PROC_HANDLES.get(pid)
    if proc is not None:
        return proc.poll() is None

    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return False
    try:
        for line in status.read_text().splitlines():
            if line.startswith("State:"):
                state = line.split()[1] if len(line.split()) > 1 else ""
                return not state.startswith("Z")
    except OSError:
        return False
    return True


def _prune_active_tasks(tasks: list) -> list:
    alive = []
    for task in tasks:
        if _is_alive(task["pid"]):
            alive.append(task)
        else:
            # Drop the Popen handle so the registry doesn't grow unbounded
            # across many launch/finish cycles in one dashboard session.
            _PROC_HANDLES.pop(task["pid"], None)
    return alive


def _start_run(settings: dict, tickers: list[str], workers: int) -> dict:
    """Spawn ``python -m app.runner`` as a detached subprocess.

    ``start_new_session=True`` puts the runner in its own process group so a
    Stop button can ``killpg(SIGTERM)`` the whole tree (the runner itself
    spawns analyst threads that share the group).
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.utcnow()
    log_path = _LOG_DIR / f"run_{started.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
    cmd = [sys.executable, "-m", "app.runner", "-j", str(workers), *tickers]
    env = _settings_to_env(settings)
    log_file = open(log_path, "w")  # noqa: SIM115 — handed to subprocess; fd duplicates
    proc = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # Hand the handle to the module-level registry so the next ``poll()`` on
    # the prune cycle reaps it cleanly instead of leaving a zombie behind.
    _PROC_HANDLES[proc.pid] = proc

    task = {
        "pid": proc.pid,
        "started_at": started.isoformat(timespec="seconds"),
        "tickers": tickers,
        "workers": workers,
        "log_path": str(log_path),
        "mode": settings["mode"],
        "deep_model": settings.get("deep_model"),
    }
    tasks = _load_active_tasks()
    tasks.append(task)
    _save_active_tasks(tasks)
    return task


def _stop_task(pid: int) -> bool:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_log_tail(path: str, n: int = 30) -> str:
    try:
        with open(path) as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(log file not yet present)"


# ─── Shared rendering helpers ───────────────────────────────────────────────

RATING_COLORS = {
    "Buy": "#16a34a",
    "Overweight": "#65a30d",
    "Hold": "#737373",
    "Underweight": "#ea580c",
    "Sell": "#dc2626",
}


def _rating_badge(rating: Optional[str]) -> str:
    if not rating:
        return "<span style='color:#9ca3af'>—</span>"
    color = RATING_COLORS.get(rating, "#374151")
    return (
        f"<span style='background:{color};color:white;padding:2px 8px;"
        f"border-radius:6px;font-size:0.85em'>{rating}</span>"
    )


def _render_decision(row) -> None:
    cols = st.columns([1, 1, 4])
    with cols[0]:
        st.markdown(f"### {row['ticker']}")
    with cols[1]:
        st.markdown(_rating_badge(row["rating"]), unsafe_allow_html=True)
    with cols[2]:
        st.caption(
            f"Trade date: {row['trade_date']} · model: "
            f"{row['model_provider']}/{row['deep_model']} · "
            f"stored: {row['created_at']}"
        )

    if row["error"]:
        st.error(row["error"])
        return

    if row["final_decision"]:
        with st.expander("Portfolio Manager decision", expanded=True):
            st.markdown(row["final_decision"])
    if row["trader_plan"]:
        with st.expander("Trader plan"):
            st.markdown(row["trader_plan"])
    if row["investment_plan"]:
        with st.expander("Research manager / debate verdict"):
            st.markdown(row["investment_plan"])

    report_fields = [
        ("Market report", row["market_report"]),
        ("Sentiment report", row["sentiment_report"]),
        ("News report", row["news_report"]),
        ("Fundamentals report", row["fundamentals_report"]),
    ]
    available = [(label, body) for label, body in report_fields if body]
    if available:
        with st.expander("Analyst reports"):
            tabs = st.tabs([label for label, _ in available])
            for tab, (_, body) in zip(tabs, available):
                with tab:
                    st.markdown(body)


# ─── Page: Decisions ────────────────────────────────────────────────────────

def page_decisions() -> None:
    st.title("Decisions")
    st.caption(f"SQLite store: `{db.get_db_path()}`")

    dates = db.list_run_dates()
    if not dates:
        st.info(
            "No decisions recorded yet. Go to the **Tasks** page to launch a run."
        )
        return

    cols = st.columns([2, 2, 2])
    selected_date = cols[0].selectbox("Trade date", dates, index=0)
    ticker_filter = cols[1].text_input("Ticker filter", "").strip().upper() or None
    cols[2].metric("Trade dates on record", len(dates))

    rows = db.list_decisions(trade_date=selected_date, ticker=ticker_filter)
    if not rows:
        st.warning("No decisions match this filter.")
        return

    success = sum(1 for r in rows if not r["error"])
    failure = len(rows) - success
    st.markdown(
        f"**{len(rows)} tickers** · {success} succeeded · {failure} failed"
    )
    for row in rows:
        _render_decision(row)
        st.divider()


# ─── Page: Tasks ────────────────────────────────────────────────────────────

def page_tasks() -> None:
    st.title("Tasks")
    settings = _load_settings()
    configured = _mode_is_configured(settings)

    cols = st.columns([1, 1, 2])
    cols[0].metric("Mode", settings["mode"].upper())
    cols[1].metric("Model", (settings.get("deep_model") or "?").split("/")[-1][:24])
    cols[2].metric("Default workers", settings.get("max_workers", 4))

    if not configured:
        st.error(
            f"Connection mode `{settings['mode']}` is missing credentials. "
            "Open **Settings** to fill them in before launching a run."
        )

    st.subheader("Start new analysis")
    default_tickers = ",".join(get_top_tickers(20))
    tickers_input = st.text_area(
        "Tickers (comma-separated)",
        value=default_tickers,
        help="Defaults to the top-20 S&P 500 constituents by market cap.",
    )
    workers = st.number_input(
        "Workers",
        min_value=1,
        max_value=16,
        value=int(settings.get("max_workers", 4)),
    )
    run_clicked = st.button(
        "Run analysis now", type="primary", disabled=not configured
    )

    if run_clicked:
        tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
        if not tickers:
            st.warning("No tickers specified.")
        else:
            task = _start_run(settings, tickers, int(workers))
            st.success(
                f"Started PID {task['pid']} on {len(tickers)} tickers. "
                f"Log: `{task['log_path']}`"
            )
            time.sleep(0.5)
            st.rerun()

    # ─── Active runs ────────────────────────────────────────────────────────
    st.divider()
    sub_header = st.container()
    active = _prune_active_tasks(_load_active_tasks())
    _save_active_tasks(active)

    sub_header.subheader(f"Active runs · {len(active)}")
    if not active:
        sub_header.caption("No active tasks. New runs launched above appear here.")
    else:
        for task in active:
            with st.container(border=True):
                started_at = datetime.fromisoformat(task["started_at"])
                elapsed = (datetime.utcnow() - started_at).total_seconds()
                hdr = st.columns([1, 1, 3, 1])
                hdr[0].markdown(f"**PID {task['pid']}**")
                hdr[1].markdown(
                    f"⏱ {int(elapsed // 60)}m{int(elapsed % 60):02d}s"
                )
                model_short = (task.get("deep_model") or "?").split("/")[-1][:30]
                hdr[2].caption(
                    f"{task['mode']} · {model_short} · "
                    f"{len(task['tickers'])} tickers · workers={task['workers']}"
                )
                if hdr[3].button("Stop", key=f"stop_{task['pid']}"):
                    if _stop_task(task["pid"]):
                        st.success(f"Sent SIGTERM to {task['pid']}.")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.warning("Task already gone.")
                st.caption(f"Tickers: {', '.join(task['tickers'])}")
                with st.expander("Log tail (last 30 lines)"):
                    st.code(_read_log_tail(task["log_path"], 30), language="text")

        if st.button("↻ Refresh active runs"):
            st.rerun()

    # ─── Recent runs from DB ────────────────────────────────────────────────
    st.divider()
    st.subheader("Recent runs (from DB)")
    runs = db.list_runs(limit=10)
    if not runs:
        st.caption("No runs recorded yet.")
        return
    for run in runs:
        elapsed_str = ""
        if run["finished_at"] and run["started_at"]:
            try:
                d1 = datetime.fromisoformat(run["started_at"])
                d2 = datetime.fromisoformat(run["finished_at"])
                seconds = int((d2 - d1).total_seconds())
                elapsed_str = f" · {seconds // 60}m{seconds % 60:02d}s"
            except ValueError:
                pass
        with st.container(border=True):
            st.markdown(
                f"**Run {run['id']}** · {run['run_date']} · "
                f"{run['success_count']}✓ / {run['failure_count']}✗{elapsed_str}"
            )
            try:
                tickers = json.loads(run["tickers"])
            except (TypeError, json.JSONDecodeError):
                tickers = []
            st.caption(f"Tickers: {', '.join(tickers)}")


# ─── Page: Settings ─────────────────────────────────────────────────────────

def page_settings() -> None:
    st.title("Settings")
    st.caption(f"Settings file: `{_SETTINGS_PATH}`")

    settings = _load_settings()

    st.subheader("Connection mode")
    mode = st.radio(
        "Choose how to reach Gonka",
        options=["router", "sdk"],
        format_func=lambda m: (
            "Router — bearer token via api.gonkascan.com" if m == "router"
            else "SDK — secp256k1 signing via public gateway"
        ),
        index=0 if settings["mode"] == "router" else 1,
        horizontal=False,
    )
    settings["mode"] = mode

    if mode == "router":
        st.subheader("Router credentials")
        settings["router_api_key"] = st.text_input(
            "GONKA_API_KEY",
            value=settings.get("router_api_key", ""),
            type="password",
            help="Bearer token issued by router.gonkascan.com dashboard.",
        )
        if settings.get("sdk_private_key") or settings.get("sdk_source_url"):
            st.caption(
                "SDK credentials are preserved on disk; the runner will "
                "ignore them while Router mode is selected."
            )
    else:
        st.subheader("SDK credentials")
        settings["sdk_private_key"] = st.text_input(
            "GONKA_PRIVATE_KEY",
            value=settings.get("sdk_private_key", ""),
            type="password",
            help="secp256k1 hex private key (`0x...` or bare hex). The "
                 "client signs each request with this key, and the gateway's "
                 "whitelisted address (auto-discovered via /v1/identity) is "
                 "used as the on-chain transfer agent.",
        )
        settings["sdk_source_url"] = st.text_input(
            "GONKA_SOURCE_URL",
            value=settings.get("sdk_source_url", "https://node4.gonka.ai"),
            help="Public inference gateway URL. The default works on Gonka mainnet.",
        )
        if settings.get("router_api_key"):
            st.caption(
                "Router API key is preserved on disk; the runner will "
                "ignore it while SDK mode is selected."
            )

    st.subheader("Model & runner")
    cols = st.columns(2)
    settings["deep_model"] = cols[0].selectbox(
        "Deep-think model",
        _MODEL_OPTIONS,
        index=(
            _MODEL_OPTIONS.index(settings["deep_model"])
            if settings.get("deep_model") in _MODEL_OPTIONS else 0
        ),
        help="Used for analyst reports, research manager, trader plan, and PM decision.",
    )
    settings["quick_model"] = cols[1].selectbox(
        "Quick-think model",
        _MODEL_OPTIONS,
        index=(
            _MODEL_OPTIONS.index(settings["quick_model"])
            if settings.get("quick_model") in _MODEL_OPTIONS else 0
        ),
        help="Used for lightweight intermediate calls; safe to leave equal to deep.",
    )
    settings["max_workers"] = int(st.number_input(
        "Default max workers",
        min_value=1,
        max_value=16,
        value=int(settings.get("max_workers", 4)),
        help="Default parallelism for the Tasks page launcher.",
    ))

    st.divider()
    if st.button("Save settings", type="primary"):
        _save_settings(settings)
        st.success(f"Saved to {_SETTINGS_PATH}.")


# ─── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="TradingAgents · Gonka",
        layout="wide",
        page_icon="📈",
    )
    db.init_db()
    _APP_HOME.mkdir(parents=True, exist_ok=True)

    pg = st.navigation(
        [
            st.Page(page_decisions, title="Decisions", icon="📊", default=True),
            st.Page(page_tasks, title="Tasks", icon="▶️"),
            st.Page(page_settings, title="Settings", icon="⚙️"),
        ]
    )
    pg.run()


main()
