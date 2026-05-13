"""Daily TradingAgents runner: analyse a list of tickers, persist to SQLite.

The runner is intentionally synchronous and ticker-serial. Running the full
multi-agent debate in parallel against the same Gonka API key risks hitting
rate limits, and the deterministic ordering makes the dashboard predictable.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
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


def run_daily(
    tickers: Optional[Iterable[str]] = None,
    *,
    trade_date: Optional[str] = None,
    top_n: int = 20,
) -> dict:
    """Run the daily analysis pass for ``tickers`` (defaults to top_n S&P 500).

    The DB is initialised lazily here so a fresh checkout's first run creates
    the schema without an explicit setup step.
    """
    db.init_db()
    tickers = list(tickers) if tickers is not None else get_top_tickers(top_n)
    if not tickers:
        return {"trade_date": trade_date, "tickers": [], "success": 0, "failure": 0}

    trade_date = trade_date or datetime.utcnow().strftime("%Y-%m-%d")
    run_id = db.start_run(run_date=trade_date, tickers=tickers)

    config = _build_config()
    # Build one graph and reuse it across tickers — the LLM clients are
    # stateless between propagate() calls, and reconstructing the LangGraph
    # for every ticker is wasteful.
    graph = TradingAgentsGraph(debug=False, config=config)

    success, failure = 0, 0
    for ticker in tickers:
        result = run_one(ticker, trade_date, graph=graph)
        if result["ok"]:
            success += 1
        else:
            failure += 1
        logger.info("[%s] %s -> %s", trade_date, ticker, result)

    db.finish_run(run_id=run_id, success=success, failure=failure)
    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "tickers": tickers,
        "success": success,
        "failure": failure,
    }


def _cli() -> int:
    """Allow ``python -m app.runner [TICKER ...]`` for ad-hoc runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = sys.argv[1:]
    tickers = [a.upper() for a in args] if args else None
    summary = run_daily(tickers)
    print(summary)
    return 0 if summary.get("failure", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
