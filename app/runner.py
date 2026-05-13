"""Daily TradingAgents runner: analyse a list of tickers, persist to SQLite.

The runner is intentionally synchronous and ticker-serial. Running the full
multi-agent debate in parallel against the same Gonka API key risks hitting
rate limits, and the deterministic ordering makes the dashboard predictable.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Iterable, Optional

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from . import db
from .sp500 import get_top_tickers


logger = logging.getLogger(__name__)


def _build_config() -> dict:
    """Resolve runtime config, defaulting Gonka + Qwen3-235B when nothing is set.

    Default model is **Qwen3-235B-Instruct** rather than the headline
    Kimi-K2.6: empirically Kimi's reasoning latency over the centralised
    router (``api.gonkascan.com``) routinely exceeds the Cloudflare 100s
    gateway timeout once TradingAgents accumulates a few agent reports into
    the prompt, producing 524s with no useful retry path. Qwen3-Instruct is
    non-reasoning, replies in single-digit seconds per call, and finishes a
    full pipeline well under that ceiling. Operators on the SDK path
    (which bypasses Cloudflare) can switch back to Kimi via env vars.

    Hard-coding ``llm_provider="gonka"`` only when the env-driven default
    config still points at ``openai`` keeps the explicit
    ``TRADINGAGENTS_LLM_PROVIDER`` escape hatch working — operators can swap to
    a different backend without editing this file.
    """
    config = DEFAULT_CONFIG.copy()
    if config.get("llm_provider") == "openai" and not os.environ.get("TRADINGAGENTS_LLM_PROVIDER"):
        config["llm_provider"] = "gonka"
        config["deep_think_llm"] = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
        config["quick_think_llm"] = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
        config["backend_url"] = None  # let GonkaClient resolve the endpoint
    return config


def _extract_reports(final_state: dict) -> dict:
    """Pull the markdown blocks the dashboard surfaces.

    Each field is optional because partial runs (e.g. a researcher debate that
    errored out before the trader stage) should still get the analyst reports
    persisted rather than blowing up the whole row.
    """
    investment_debate = final_state.get("investment_debate_state") or {}
    return {
        "market_report":        final_state.get("market_report"),
        "sentiment_report":     final_state.get("sentiment_report"),
        "news_report":          final_state.get("news_report"),
        "fundamentals_report":  final_state.get("fundamentals_report"),
        "investment_plan":      final_state.get("investment_plan") or investment_debate.get("judge_decision"),
        "trader_plan":          final_state.get("trader_investment_plan"),
    }


def run_one(
    ticker: str,
    trade_date: str,
    *,
    graph: Optional[TradingAgentsGraph] = None,
) -> dict:
    """Run the agent graph for one ticker and persist the result.

    Returns a small dict describing the outcome so the caller can summarise
    the batch without re-querying the DB.
    """
    config = _build_config()
    logger.info(
        "Starting %s on %s via %s/%s",
        ticker, trade_date, config.get("llm_provider"), config.get("deep_think_llm"),
    )
    ta = graph or TradingAgentsGraph(debug=False, config=config)

    try:
        logger.info("[%s] entering propagate (multi-agent debate + tools)", ticker)
        final_state, _signal = ta.propagate(ticker, trade_date)
        logger.info("[%s] propagate complete", ticker)
        final_decision = final_state.get("final_trade_decision") or ""
        rating = parse_rating(final_decision) if final_decision else None
        db.upsert_decision(
            ticker=ticker,
            trade_date=trade_date,
            rating=rating,
            final_decision=final_decision,
            reports=_extract_reports(final_state),
            model_provider=config.get("llm_provider"),
            deep_model=config.get("deep_think_llm"),
            quick_model=config.get("quick_think_llm"),
        )
        return {"ticker": ticker, "ok": True, "rating": rating}
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        logger.exception("Run failed for %s on %s", ticker, trade_date)
        db.upsert_decision(
            ticker=ticker,
            trade_date=trade_date,
            rating=None,
            final_decision=None,
            reports={},
            model_provider=config.get("llm_provider"),
            deep_model=config.get("deep_think_llm"),
            quick_model=config.get("quick_think_llm"),
            error=f"{type(exc).__name__}: {exc}\n{tb}",
        )
        return {"ticker": ticker, "ok": False, "error": str(exc)}


def _env_max_workers(default: int = 1) -> int:
    raw = os.environ.get("TRADINGAGENTS_APP_MAX_WORKERS")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid TRADINGAGENTS_APP_MAX_WORKERS=%r — using %d", raw, default)
        return default


def run_daily(
    tickers: Optional[Iterable[str]] = None,
    *,
    trade_date: Optional[str] = None,
    top_n: int = 20,
    max_workers: Optional[int] = None,
) -> dict:
    """Run the daily analysis pass for ``tickers`` (defaults to top_n S&P 500).

    When ``max_workers > 1``, ticker analyses run concurrently in a thread
    pool. **Each worker constructs its own TradingAgentsGraph** because the
    graph object accumulates per-ticker state on ``self.curr_state`` /
    ``self.log_states_dict`` / ``self.ticker`` during ``propagate()``;
    sharing one graph across threads would interleave that state and
    corrupt the persisted JSON state logs.

    ``max_workers`` precedence: explicit arg → ``TRADINGAGENTS_APP_MAX_WORKERS``
    env → 1 (serial). The yfinance + FinnHub data sources rate-limit
    aggressively, and each Gonka TA has its own bandwidth cap (returns
    HTTP 429 above it), so values above ~8 risk degraded throughput from
    upstream pushback rather than higher concurrency.

    The DB is initialised lazily here so a fresh checkout's first run creates
    the schema without an explicit setup step.
    """
    db.init_db()
    tickers = list(tickers) if tickers is not None else get_top_tickers(top_n)
    if not tickers:
        return {"trade_date": trade_date, "tickers": [], "success": 0, "failure": 0}

    trade_date = trade_date or datetime.utcnow().strftime("%Y-%m-%d")
    run_id = db.start_run(run_date=trade_date, tickers=tickers)

    if max_workers is None:
        max_workers = _env_max_workers(default=1)
    max_workers = max(1, min(max_workers, len(tickers)))

    config = _build_config()
    started = time.monotonic()
    logger.info(
        "Daily run start: %d tickers, max_workers=%d, provider=%s, model=%s",
        len(tickers), max_workers, config.get("llm_provider"), config.get("deep_think_llm"),
    )

    success, failure = 0, 0

    def _task(ticker: str) -> dict:
        # One graph per task. Building it is cheap relative to the
        # propagate() cost (a handful of imports + memory log open), and
        # keeping it inside the task means each thread owns its own
        # curr_state / log_states_dict without locking.
        local_graph = TradingAgentsGraph(debug=False, config=config)
        return run_one(ticker, trade_date, graph=local_graph)

    if max_workers == 1:
        # Preserve the serial path verbatim — easier to debug and identical
        # to pre-concurrency behavior.
        for ticker in tickers:
            result = _task(ticker)
            if result["ok"]:
                success += 1
            else:
                failure += 1
            logger.info("[%s] %s -> %s", trade_date, ticker, result)
    else:
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ticker"
        ) as pool:
            futures = {pool.submit(_task, t): t for t in tickers}
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    # run_one already catches and persists per-ticker errors,
                    # so the only way to land here is a bug in graph construction
                    # itself — log and count as failure.
                    logger.exception("Task wrapper failed for %s", ticker)
                    result = {"ticker": ticker, "ok": False, "error": str(exc)}
                if result["ok"]:
                    success += 1
                else:
                    failure += 1
                logger.info("[%s] %s -> %s", trade_date, ticker, result)

    elapsed = time.monotonic() - started
    logger.info(
        "Daily run finished in %.1fs (%d ok / %d fail across %d tickers)",
        elapsed, success, failure, len(tickers),
    )
    db.finish_run(run_id=run_id, success=success, failure=failure)
    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "tickers": tickers,
        "success": success,
        "failure": failure,
        "elapsed_seconds": round(elapsed, 1),
    }


def _cli() -> int:
    """Allow ``python -m app.runner [-j N] [TICKER ...]`` for ad-hoc runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="app.runner",
        description="Run the TradingAgents daily analysis pass.",
    )
    parser.add_argument(
        "-j", "--workers", type=int, default=None,
        help="number of concurrent tickers (default: env TRADINGAGENTS_APP_MAX_WORKERS or 1)",
    )
    parser.add_argument(
        "tickers", nargs="*",
        help="tickers to analyse; empty = top-20 S&P 500 from app.sp500",
    )
    ns = parser.parse_args()
    tickers = [t.upper() for t in ns.tickers] if ns.tickers else None
    summary = run_daily(tickers, max_workers=ns.workers)
    print(summary)
    return 0 if summary.get("failure", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
