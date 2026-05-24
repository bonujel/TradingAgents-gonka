"""Join the llm_debug.jsonl stream with the decisions DB and surface empty-content failures.

Reads:
  * ``~/.tradingagents/app/logs/llm_debug.jsonl``     — one record per LLM invocation
  * ``~/.tradingagents/app/decisions.sqlite3``        — one row per (ticker, trade_date)

Produces a per-ticker breakdown:
  * Each LLM call as a row: ts, node, step, finish_reason, content_len, prompt_chars,
    elapsed_s, error
  * Counts of empty-content events per node
  * Cross-reference: for each ticker's DB row, which fields are empty + which
    llm_debug.jsonl record(s) caused them

Intentionally read-only. Doesn't change DB, doesn't change jsonl. Safe to run
mid-run.

Usage:
    python scripts/analyze_kimi_run.py [--trade-date 2026-05-23] [--since-ts 2026-05-23T15:23]

If --since-ts is given, only jsonl records with ts_start >= since are
considered. Useful when llm_debug.jsonl has older Qwen entries appended to it
from earlier runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOG_PATH = Path.home() / ".tradingagents" / "app" / "logs" / "llm_debug.jsonl"
DB_PATH = Path.home() / ".tradingagents" / "app" / "decisions.sqlite3"


# ── helpers ────────────────────────────────────────────────────────────


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # python doesn't accept the trailing 'Z'
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _node_key(rec: dict) -> str:
    md = rec.get("metadata") or {}
    return md.get("langgraph_node") or "(unknown)"


def _content_of(rec: dict) -> str:
    resp = rec.get("response") or {}
    gens = resp.get("generations") or []
    if not gens:
        return ""
    first = gens[0]
    text = first.get("content") or ""
    return text


def _finish_reason_of(rec: dict) -> str | None:
    resp = rec.get("response") or {}
    gens = resp.get("generations") or []
    if not gens:
        return None
    first = gens[0]
    ginfo = first.get("generation_info") or {}
    rmeta = first.get("response_metadata") or {}
    return ginfo.get("finish_reason") or rmeta.get("finish_reason")


def _has_tool_calls(rec: dict) -> bool:
    resp = rec.get("response") or {}
    gens = resp.get("generations") or []
    if not gens:
        return False
    return bool(gens[0].get("tool_calls"))


def _prompt_chars(rec: dict) -> int:
    """Sum of every message's content length (excludes tool_calls)."""
    total = 0
    for msg in rec.get("messages") or []:
        c = msg.get("content")
        if isinstance(c, str):
            total += len(c)
    return total


def _ticker_from_messages(rec: dict) -> str | None:
    """Pull the ticker from prompt text by simple heuristics.

    The llm_debug record itself does NOT carry the ticker — it's a LangChain
    callback that sees only the chat messages. But for analyst calls the
    system prompt mentions "The instrument to analyze is `XXX`" or
    "instrument context ... XXX"; for downstream nodes the ticker is in
    the user prompt. We grep both for the first ASCII ticker token we know
    about from the active_tasks.json — or fall back to None and let the
    caller dispatch by step ordering.
    """
    text = " ".join(
        (m.get("content") or "") for m in (rec.get("messages") or [])
        if isinstance(m.get("content"), str)
    )
    # Look for the literal ``\``X\``` pattern used in instrument context.
    import re
    m = re.search(r"instrument to analyze is `([A-Z]{1,8}(?:[.\-][A-Z]{1,5})?)`", text)
    if m:
        return m.group(1)
    m = re.search(r"\bfor\s+([A-Z]{1,8}(?:[.\-][A-Z]{1,5})?)\b", text)
    if m:
        return m.group(1)
    return None


# ── load ───────────────────────────────────────────────────────────────


def load_jsonl(since: datetime | None) -> list[dict]:
    rows: list[dict] = []
    if not LOG_PATH.exists():
        return rows
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is not None:
            ts = _parse_iso(r.get("ts_start") or "")
            if ts is None or ts < since:
                continue
        rows.append(r)
    return rows


def load_db_rows(trade_date: str | None) -> list[dict]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        q = """SELECT ticker, trade_date, rating,
                      length(coalesce(final_decision,'')) AS final_len,
                      length(coalesce(market_report,'')) AS market_len,
                      length(coalesce(sentiment_report,'')) AS sent_len,
                      length(coalesce(news_report,'')) AS news_len,
                      length(coalesce(fundamentals_report,'')) AS fund_len,
                      length(coalesce(investment_plan,'')) AS plan_len,
                      length(coalesce(trader_plan,'')) AS trader_len,
                      length(coalesce(error,'')) AS err_len,
                      created_at
               FROM decisions"""
        params: list = []
        if trade_date:
            q += " WHERE trade_date = ?"
            params.append(trade_date)
        q += " ORDER BY ticker"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ── analysis ───────────────────────────────────────────────────────────


def per_call_table(rows: list[dict]) -> None:
    """Print every jsonl record as one summary line."""
    print(
        f"{'#':>3}  {'ts':<25}  {'node':<22}  {'step':>4}  "
        f"{'finish':<11}  {'tools':<5}  {'content':>7}  {'prompt':>7}  "
        f"{'elapsed_s':>9}  ticker"
    )
    print("-" * 130)
    for i, r in enumerate(rows):
        md = r.get("metadata") or {}
        node = _node_key(r)
        step = md.get("langgraph_step")
        fr = _finish_reason_of(r) or "?"
        tools = "Y" if _has_tool_calls(r) else "-"
        content = _content_of(r)
        cl = len(content)
        pl = _prompt_chars(r)
        el = (r.get("elapsed_ms") or 0) / 1000.0
        empty_mark = " EMPTY" if cl == 0 and fr != "tool_calls" else ""
        length_mark = " LENGTH" if fr == "length" else ""
        tk = _ticker_from_messages(r) or "?"
        print(
            f"{i:>3}  {(r.get('ts_start') or '')[:19]:<25}  {node[:22]:<22}  "
            f"{step!s:>4}  {fr:<11}  {tools:<5}  {cl:>7}  {pl:>7}  "
            f"{el:>9.1f}  {tk}{empty_mark}{length_mark}"
        )


def empty_content_breakdown(rows: list[dict]) -> None:
    """Group empty-content events by node, with prompt-size + finish_reason context."""
    by_node: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if _has_tool_calls(r):  # tool-call calls naturally have empty content
            continue
        if len(_content_of(r)) == 0:
            by_node[_node_key(r)].append(r)
    if not by_node:
        print("\n[empty-content] none observed\n")
        return
    print("\n[empty-content] events by node:")
    for node, lst in sorted(by_node.items(), key=lambda kv: -len(kv[1])):
        print(f"  {node}: {len(lst)}")
        for r in lst[:5]:  # cap per node
            fr = _finish_reason_of(r)
            pl = _prompt_chars(r)
            el = (r.get("elapsed_ms") or 0) / 1000.0
            print(f"    - step={(r.get('metadata') or {}).get('langgraph_step')!s}  finish={fr}  prompt_chars={pl}  elapsed={el:.1f}s")


def finish_reason_breakdown(rows: list[dict]) -> None:
    counts: dict[str | None, int] = defaultdict(int)
    for r in rows:
        counts[_finish_reason_of(r)] += 1
    print("\n[finish_reason] tally:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k!s:<14} : {v}")


def error_breakdown(rows: list[dict]) -> None:
    err_rows = [r for r in rows if r.get("error")]
    if not err_rows:
        print("\n[errors] none (no on_llm_error events)")
        return
    print(f"\n[errors] {len(err_rows)} on_llm_error event(s):")
    for r in err_rows[:20]:
        e = r.get("error") or {}
        print(f"  step={(r.get('metadata') or {}).get('langgraph_step')!s}  node={_node_key(r)}  {e.get('type')}: {(e.get('message') or '')[:120]}")


def db_field_breakdown(rows: list[dict]) -> None:
    if not rows:
        print("\n[db] no rows for the given trade_date")
        return
    print("\n[db] decision rows:")
    print(
        f"  {'ticker':<8} {'rating':<12} {'final':>6} "
        f"{'mkt':>5} {'sent':>5} {'news':>5} {'fund':>5} {'plan':>5} {'trd':>5} {'err':>5}"
    )
    for r in rows:
        print(
            f"  {r['ticker']:<8} {(r['rating'] or '<NULL>'):<12} "
            f"{r['final_len']:>6} "
            f"{r['market_len']:>5} {r['sent_len']:>5} {r['news_len']:>5} "
            f"{r['fund_len']:>5} {r['plan_len']:>5} {r['trader_len']:>5} {r['err_len']:>5}"
        )


# ── main ───────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trade-date", default=None,
                   help="filter DB rows by trade_date (default: all)")
    p.add_argument("--since-ts", default=None,
                   help="filter jsonl by ts_start >= ISO timestamp (e.g. 2026-05-23T15:23)")
    p.add_argument("--max-rows", type=int, default=200,
                   help="limit number of jsonl rows printed in the per-call table")
    p.add_argument("--no-per-call", action="store_true",
                   help="skip the per-call table (just the summaries)")
    args = p.parse_args()

    since = _parse_iso(args.since_ts) if args.since_ts else None

    jsonl_rows = load_jsonl(since)
    db_rows = load_db_rows(args.trade_date)

    print(f"jsonl: {len(jsonl_rows)} records  | since: {args.since_ts or '(all)'}")
    print(f"db   : {len(db_rows)} rows         | trade_date: {args.trade_date or '(all)'}")
    print()
    if not args.no_per_call:
        per_call_table(jsonl_rows[: args.max_rows])
    finish_reason_breakdown(jsonl_rows)
    empty_content_breakdown(jsonl_rows)
    error_breakdown(jsonl_rows)
    db_field_breakdown(db_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
