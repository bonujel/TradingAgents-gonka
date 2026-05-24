"""Probe Kimi-K2.6 token capacity on Gonka router.

Goal: empirically answer "at what `max_tokens` budget does Kimi-K2.6 stop
returning empty `content` on PM/Trader-shaped prompts?"

Hypothesis under test (from
``dev_notes/debug_kimi_no_rating_output/kimi-empty-content-silent-failure-2026-05-23.md``):

  Kimi spends most of `max_tokens` on the `reasoning` field, and when
  ``max_tokens=8192`` runs out mid-reasoning, vLLM returns
  ``finish_reason="length"`` with ``content=""``. The 200 OK + empty
  content sneaks past LangGraph and lands in the DB.

What this script does:

  * Bypasses LangChain entirely and talks to ``api.gonkascan.com/v1``
    directly via the openai SDK, so we read the raw ``finish_reason`` and
    the raw ``reasoning_content`` chunk-by-chunk.
  * Sweeps ``max_tokens`` × prompt-shape and prints a table:
        prompt | budget | finish | content_len | reasoning_len | usage tokens | elapsed
  * Uses streaming with ``stream_options={"include_usage": True}`` because
    Gonka's API node only proxies SSE chunk-by-chunk; non-streamed JSON
    gets buffered past Cloudflare's 100s upstream timeout
    (gonka_client.py:46-58).

What this script does NOT do:

  * No production-code edits. Read-only probe.
  * No DB writes. Output goes to stdout + ``dev_notes/debug_kimi_no_rating_output/``.
  * No retry loop — we want to see the raw failure mode, not paper over it.

Usage:

    python scripts/probe_kimi_max_tokens.py
    python scripts/probe_kimi_max_tokens.py --budgets 8192,16384,32768
    python scripts/probe_kimi_max_tokens.py --prompts trader,pm

Reads ``GONKA_API_KEY`` from env, or falls back to
``~/.tradingagents/app/settings.json`` so it works without re-exporting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

import openai


ROUTER_BASE_URL = "https://api.gonkascan.com/v1"
MODEL = "moonshotai/Kimi-K2.6"
OUT_DIR = Path("dev_notes/debug_kimi_no_rating_output")
JSONL_PATH = OUT_DIR / f"probe_max_tokens_{dt.datetime.utcnow():%Y%m%d_%H%M%S}.jsonl"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("probe_kimi")


# ── prompt shapes ───────────────────────────────────────────────────────
#
# These approximate what the three real agents send. The actual production
# prompts are longer (full debate history, past-context lessons, etc.) —
# we use bounded synthetic prompts so the sweep is reproducible and so we
# can isolate "what does Kimi do at this exact length" without DB state
# coupling.

_SHORT_TRADER = """\
You are a trading agent. Recommend buy/sell/hold for AVGO.

**Required Output Format**:

**Action**: <Buy/Hold/Sell>

**Reasoning**: <2-4 sentences>

FINAL TRANSACTION PROPOSAL: **BUY** (or **SELL** or **HOLD**)

The investment plan: AVGO has strong AI demand, but valuation is stretched.
P/E is 32x forward, vs sector at 22x. Capital returns are robust.
"""

_MEDIUM_PM = """\
As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision for AVGO.

**Rating Scale**:
- **Buy**: Strong conviction
- **Overweight**: Favorable outlook
- **Hold**: Maintain
- **Underweight**: Reduce
- **Sell**: Exit

**Context:**
- Research Manager's plan: AVGO is trading at 32x forward P/E vs sector at 22x.
  AI semiconductor demand remains strong, with hyperscaler capex growth
  exceeding 30% YoY. The dividend yield is 1.4% and capital returns are
  consistent. Bear thesis centers on valuation rerating risk if AI spend
  cycles peak in 2026; bull thesis on durable share gains in custom silicon
  with major hyperscaler design wins. Recommend Overweight with
  position sizing at 4% portfolio weight, targeting $1450 with downside
  to $1100. Time horizon 6-12 months.
- Trader's proposal: Action Buy, Entry $1280, Stop $1100, sizing 4%.

**Risk Analysts Debate:**

Aggressive (round 1): The semis cycle has multi-quarter runway. P/E premium
is justified by 25%+ EPS growth and improving FCF margins. Add 4% on any
weakness toward $1250.

Conservative (round 1): Valuation rerating risk is acute. AI hardware spend
will peak in 2026 as hyperscalers digest existing capacity. Trim to 2% or
exit; the asymmetric downside outweighs incremental upside.

Neutral (round 1): Both sides have merit. Recommend phased entry at $1280
and $1200 levels, with stop discipline at $1100, total weight capped at 3%.

Aggressive (round 2): The conservative case underestimates inference demand.
Custom silicon market is growing 50% YoY, AVGO has dominant share.
Bet size should reflect conviction; 4% is appropriate.

Conservative (round 2): Inference demand is real but Nvidia has competitive
moat. AVGO custom silicon is single-customer concentrated. Trim risk.

Neutral (round 2): Phased entry mitigates path-dependency. 3% target weight
with discipline at stops.

**Required Output Format**:

**Rating**: <Buy / Overweight / Hold / Underweight / Sell>

**Executive Summary**: <2-4 sentences>

**Investment Thesis**: <detailed reasoning anchored in the debate>

The first line of your response MUST begin with ``**Rating**:`` followed
by exactly one of the five tier names.
"""

_LONG_PM = _MEDIUM_PM + "\n\n**Additional Past Context (lessons from prior decisions):**\n" + (
    "- On 2026-04-15 the same AVGO debate concluded with Buy at $1180. "
    "Outcome: +9% in 30 days, gross alpha vs SOX of +4%. Lesson: when "
    "hyperscaler capex prints exceed consensus by 5%+ in a single quarter, "
    "the rerating signal is durable for ~60 days.\n"
) * 30  # roughly multiplies the past-context block to PM-realistic length


# pm_xl approximates a production-shaped PM prompt: ~12k chars / ~3k prompt
# tokens of debate history + past lessons. Mirrors the long tail observed in
# kimi-run-23 (where some PM prompts crossed 10k chars after multi-round
# risk debate with verbose Kimi reasoning).
_XL_PM = _MEDIUM_PM + "\n\n**Extended Risk Analysts Debate (rounds 3-6):**\n" + (
    "\nAggressive (round N): The structural growth story in custom silicon "
    "is underappreciated. AVGO's Tomahawk and Jericho switching ASICs are "
    "irreplaceable in hyperscaler fabric. Capex YoY growth is accelerating, "
    "not decelerating. Trim positions only at >40x forward P/E, we're at 32x. "
    "Asymmetric upside if FY27 EPS prints above $80.\n\n"
    "Conservative (round N): The peak-cycle argument is empirically grounded. "
    "Every prior semis cycle (2000, 2008, 2018, 2022) saw 30-50% drawdowns "
    "from peak multiples. AVGO's ~30% rerate since Jan is unsustainable. "
    "Initial position should be 1.5%, scaling only on confirmation.\n\n"
    "Neutral (round N): The disagreement is fundamentally about cycle timing. "
    "Phased entry at $1280, $1200, $1100 with 1.5% each tranche provides "
    "average-down optionality. Hard stop at $1050 on 60-day trailing.\n\n"
) * 8 + "\n\n**Past Context Lessons:**\n" + (
    "- 2026-03-12 AVGO debate concluded Buy at $1090. Outcome: +12% in 21 days. "
    "Lesson: AVGO custom-silicon design-win catalysts cluster around earnings; "
    "entries 2 weeks pre-print outperformed 5-day-pre-print by 4pp.\n"
    "- 2026-02-08 AVGO debate concluded Hold at $1240. Outcome: -3% in 30 days. "
    "Lesson: Hold ratings during multiple-expansion phases tend to underperform "
    "; conviction Buy preserves more upside even with tighter stops.\n"
    "- 2026-01-22 AVGO debate concluded Overweight at $1180. Outcome: +6% in 14 days. "
    "Lesson: Overweight rated positions with phased 1.5%+1.5% entries captured "
    "85% of the move with 60% of the max drawdown of full-size Buy positions.\n"
) * 25


PROMPTS: dict[str, str] = {
    "trader_short": _SHORT_TRADER,
    "pm_medium": _MEDIUM_PM,
    "pm_long": _LONG_PM,
    "pm_xl": _XL_PM,
}


# ── budgets ────────────────────────────────────────────────────────────

DEFAULT_BUDGETS = [4096, 8192, 16384, 32768, 65536]


# ── client ─────────────────────────────────────────────────────────────


def _load_api_key() -> str:
    key = os.environ.get("GONKA_API_KEY")
    if key:
        return key
    settings_path = Path.home() / ".tradingagents" / "app" / "settings.json"
    if settings_path.exists():
        try:
            cfg = json.loads(settings_path.read_text())
            if cfg.get("router_api_key"):
                return cfg["router_api_key"]
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to read %s: %s", settings_path, exc)
    raise SystemExit(
        "Need GONKA_API_KEY in env or router_api_key in "
        "~/.tradingagents/app/settings.json"
    )


def _make_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=ROUTER_BASE_URL,
        api_key=_load_api_key(),
        timeout=180.0,
    )


# ── single probe call ──────────────────────────────────────────────────


def _run_one(
    client: openai.OpenAI,
    prompt_name: str,
    prompt_body: str,
    max_tokens: int,
    attempt: int,
) -> dict:
    """One streamed call. Returns a dict with finish_reason, lengths, usage.

    A per-call ``Cache-buster:`` line is prepended to the prompt. The Gonka
    router caches on the prompt + core OpenAI params, so without this every
    repeat run returns the identical cached payload (verified empirically:
    same content_len + same completion_tokens across 5 budgets in the first
    sweep on 2026-05-23). A UUID line at the top of the user message is
    semantically invisible to Kimi but is enough to defeat the cache.
    """

    cache_buster = f"Cache-buster: {uuid.uuid4().hex} attempt={attempt}\n\n"
    user_content = cache_buster + prompt_body

    record = {
        "ts_start": dt.datetime.utcnow().isoformat() + "Z",
        "prompt": prompt_name,
        "prompt_chars": len(user_content),
        "max_tokens": max_tokens,
        "attempt": attempt,
        "model": MODEL,
    }

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = None
    usage = None
    error: str | None = None

    t0 = time.perf_counter()
    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if not chunk.choices:
                # Final usage chunk: choices=[] and usage is set
                if chunk.usage is not None:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
            # vLLM puts CoT in `reasoning_content` for reasoning models.
            # The openai SDK exposes it via the model_extra dict.
            extra = getattr(delta, "model_extra", None) or {}
            r = extra.get("reasoning_content") or extra.get("reasoning")
            if r:
                reasoning_parts.append(r)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    elapsed = time.perf_counter() - t0
    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)

    record.update(
        {
            "ts_end": dt.datetime.utcnow().isoformat() + "Z",
            "elapsed_s": round(elapsed, 2),
            "finish_reason": finish_reason,
            "content_len": len(content),
            "reasoning_len": len(reasoning),
            "content_head": content[:240],
            "reasoning_head": reasoning[:240],
            "usage": usage,
            "error": error,
        }
    )
    return record


# ── driver ─────────────────────────────────────────────────────────────


def _row(rec: dict) -> str:
    empty = rec["content_len"] == 0
    flag = "  ⚠ EMPTY" if empty else ""
    return (
        f"{rec['prompt']:<14} "
        f"budget={rec['max_tokens']:>6}  "
        f"att={rec['attempt']}  "
        f"finish={str(rec['finish_reason']):<10}  "
        f"content={rec['content_len']:>6}  "
        f"reasoning={rec['reasoning_len']:>6}  "
        f"toks={rec.get('usage') or {}}  "
        f"{rec['elapsed_s']}s"
        + (f"  ERR={rec['error']}" if rec.get("error") else "")
        + flag
    )


def run(prompts: Iterable[str], budgets: Iterable[int], repeats: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = _make_client()
    log.info("logging to %s", JSONL_PATH)
    log.info("model=%s, base=%s, repeats=%d", MODEL, ROUTER_BASE_URL, repeats)

    with JSONL_PATH.open("a") as fp:
        for pname in prompts:
            body = PROMPTS[pname]
            log.info("--- prompt=%s chars=%d ---", pname, len(body))
            for budget in budgets:
                for attempt in range(1, repeats + 1):
                    rec = _run_one(client, pname, body, budget, attempt)
                    fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fp.flush()
                    print(_row(rec))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--budgets",
        default=",".join(str(b) for b in DEFAULT_BUDGETS),
        help="comma-separated max_tokens budgets",
    )
    p.add_argument(
        "--prompts",
        default=",".join(PROMPTS.keys()),
        help=f"comma-separated prompt names: {','.join(PROMPTS)}",
    )
    p.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="independent attempts per (prompt, budget) combo with fresh cache-busters",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    budgets = [int(b) for b in args.budgets.split(",") if b.strip()]
    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    unknown = [p for p in prompts if p not in PROMPTS]
    if unknown:
        raise SystemExit(f"unknown prompts: {unknown}; available: {list(PROMPTS)}")
    run(prompts, budgets, args.repeats)
