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
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator, Optional

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.retry import is_transient_llm_error

from . import db
from .sp500 import get_top_tickers


logger = logging.getLogger(__name__)


# --- Retry policy -----------------------------------------------------------
#
# The 2026-05-15 S&P 500 batch (see dev_notes/sp500-run-2026-05-15.md) showed
# 40% failure rate, with ~80% of failures attributable to transient router-side
# issues (peer closed connections, 502 Bad Gateway) and another ~14% to a
# Gonka chain executor bug (winner inference incomplete / nonce_finished=false).
#
# Such failures are now retried at two granularities:
#
#   * Node-level — a LangGraph RetryPolicy on every graph node re-runs only
#     the failing node, keeping every prior node's committed state. This is
#     the cheap first line of defence (see tradingagents/graph/setup.py).
#   * Ticker-level — the loop in run_one() below re-runs the whole pipeline
#     as a coarse backstop for anything the node layer could not recover.
#
# Both layers share one classifier, ``is_transient_llm_error``, so they
# agree on what counts as recoverable. ``_is_retryable`` is kept as a
# module-local alias for readability at the call site.

_is_retryable = is_transient_llm_error


class _NodeRetryCounter(logging.Handler):
    """Counts LangGraph node-level retries by tapping its retry logger.

    Node retries happen inside ``graph.invoke`` and are opaque to the
    caller — the only seam to observe them is the
    ``langgraph.pregel._retry`` logger, which emits
    ``"Retrying task <name> after <s>s (attempt N) ..."`` on each retry.
    Counting those records lets ``run_one`` report honestly how hard a
    row was tried, including the retries the ``[ticker-retries=N]``
    counter cannot see. Coupled to that one log prefix; if LangGraph
    rewords it the count silently reads zero — the run still behaves
    correctly, only the diagnostic number is lost.
    """

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.getMessage().startswith("Retrying task"):
                self.count += 1
        except Exception:  # noqa: BLE001 — a logging handler must never raise
            pass


@contextmanager
def _count_node_retries() -> Iterator[_NodeRetryCounter]:
    counter = _NodeRetryCounter()
    lg_logger = logging.getLogger("langgraph.pregel._retry")
    lg_logger.addHandler(counter)
    try:
        yield counter
    finally:
        lg_logger.removeHandler(counter)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r — using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("Invalid %s=%r — using %.1f", name, raw, default)
        return default


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a hard cap.

    ``attempt`` is 1-indexed: 1 = first retry (i.e. first sleep), 2 = second
    retry, etc. Defaults give 5s → 15s → 45s, which is wide enough to clear
    the typical router-blip cluster (failures arrive in 1-minute bursts of
    4–6 tickers per the May-15 data). Caller can tune via env vars:

      TRADINGAGENTS_APP_RETRY_BACKOFF      first sleep, in seconds (default 5)
      TRADINGAGENTS_APP_RETRY_BACKOFF_MAX  cap per sleep, in seconds (default 60)
    """
    base = _env_float("TRADINGAGENTS_APP_RETRY_BACKOFF", 5.0)
    cap = _env_float("TRADINGAGENTS_APP_RETRY_BACKOFF_MAX", 60.0)
    return min(base * (3 ** (attempt - 1)), cap)


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
    max_retries: Optional[int] = None,
) -> dict:
    """Run the agent graph for one ticker and persist the result.

    On a retryable failure (see ``_is_retryable``), we sleep with exponential
    backoff and try again, up to ``max_retries`` extra attempts (default 3
    via env ``TRADINGAGENTS_APP_MAX_RETRIES``). Each retry builds a *fresh*
    ``TradingAgentsGraph`` because the graph object accumulates per-ticker
    state on ``self.curr_state`` / ``self.log_states_dict`` during
    ``propagate()``; reusing a dirtied graph would carry partial-state
    side effects into the next attempt and defeat the point of retrying.

    This ticker-level loop is only the coarse backstop. Most transient
    failures are already recovered one layer down by the LangGraph
    node-level RetryPolicy inside ``propagate()`` — so by the time an
    exception reaches here, the node-level retries are *already
    exhausted*. The persisted error is prefixed with
    ``[ticker-retries=N node-retries=M]`` so a dashboard scan shows both:
    M proves the row was re-rolled at the node level even when N is 0
    (e.g. degenerate output, which is intentionally not ticker-retried).
    The (ticker, trade_date) row is UPSERT'd so a later successful retry
    overwrites any earlier failure row from the same call.

    Returns a small dict describing the outcome so the caller can summarise
    the batch without re-querying the DB.
    """
    config = _build_config()
    if max_retries is None:
        max_retries = _env_int("TRADINGAGENTS_APP_MAX_RETRIES", 3)
    total_attempts = 1 + max_retries

    logger.info(
        "Starting %s on %s via %s/%s (max_retries=%d)",
        ticker, trade_date, config.get("llm_provider"),
        config.get("deep_think_llm"), max_retries,
    )

    last_exc: Optional[BaseException] = None
    last_tb: str = ""
    attempt = 0

    # The counter spans every ticker attempt, so node-level retries are
    # tallied across the whole call — not reset per ticker-level retry.
    with _count_node_retries() as node_retries:
        for attempt in range(total_attempts):
            # Honour a caller-supplied graph only on the very first attempt;
            # every retry builds its own clean graph (see docstring).
            ta = graph if (attempt == 0 and graph is not None) \
                else TradingAgentsGraph(debug=False, config=config)

            try:
                if attempt == 0:
                    logger.info("[%s] entering propagate (multi-agent debate + tools)", ticker)
                else:
                    logger.info(
                        "[%s] retry %d/%d (fresh graph)", ticker, attempt, max_retries,
                    )
                final_state, _signal = ta.propagate(ticker, trade_date)
                logger.info("[%s] propagate complete on attempt %d", ticker, attempt + 1)
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
                return {
                    "ticker": ticker, "ok": True, "rating": rating,
                    "retries": attempt, "node_retries": node_retries.count,
                }
            except Exception as exc:
                # Capture traceback inside the except block — sys.exc_info()
                # is only populated here, and we need the text below.
                last_exc = exc
                last_tb = traceback.format_exc(limit=4)
                retryable = _is_retryable(exc)
                if retryable and attempt < max_retries:
                    delay = _backoff_seconds(attempt + 1)
                    logger.warning(
                        "[%s] attempt %d/%d failed (%s: %s); sleeping %.1fs before retry",
                        ticker, attempt + 1, total_attempts,
                        type(exc).__name__, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                # Non-retryable or out of retries — persist the failure.
                break

    # All attempts exhausted (or hit a non-retryable exception). Both retry
    # counts are persisted: ticker-retries is the whole-pipeline retry loop
    # above; node-retries is how many times LangGraph re-ran an individual
    # node inside propagate(). A degenerate-output failure shows
    # ticker-retries=0 (excluded from the ticker loop by design) but a
    # non-zero node-retries — the node *was* re-rolled before giving up.
    assert last_exc is not None  # loop must have entered the except branch
    logger.exception(
        "Run failed for %s on %s after %d ticker attempt(s), %d node retr(ies)",
        ticker, trade_date, attempt + 1, node_retries.count,
    )
    error_msg = (
        f"[ticker-retries={attempt} node-retries={node_retries.count}] "
        f"{type(last_exc).__name__}: {last_exc}\n{last_tb}"
    )
    db.upsert_decision(
        ticker=ticker,
        trade_date=trade_date,
        rating=None,
        final_decision=None,
        reports={},
        model_provider=config.get("llm_provider"),
        deep_model=config.get("deep_think_llm"),
        quick_model=config.get("quick_think_llm"),
        error=error_msg,
    )
    return {
        "ticker": ticker, "ok": False, "error": str(last_exc),
        "retries": attempt, "node_retries": node_retries.count,
    }


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
