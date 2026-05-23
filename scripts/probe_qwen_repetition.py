"""Probe Qwen-on-Gonka for repetition / token-salad output and find the
generation-parameter combo that suppresses it.

Background
----------
Qwen3-235B-A22B-Instruct-2507-FP8 served through Gonka's vLLM occasionally
collapses into degenerate output during Market Analyst calls — the model
loses coherence and emits long runs of repeated punctuation, single-token
loops, or near-random "token salad" (observed: ``_G_A. |A A A G_G_AI _ _
a_A: A_A: ...``). vLLM's guided decoding flags don't apply here (free-text
path), so this is a sampling / decoding issue, not a schema issue.

The Gonka client currently forwards none of the OpenAI penalty parameters
and no ``extra_body``. This probe bypasses that gap by talking directly
to the Gonka router with a sweep of generation configs, runs N samples
per config, scores each completion with the existing
:func:`tradingagents.agents.utils.degeneracy.is_degenerate_text` detector,
and writes one JSONL row per trial plus a summary table at the end.

Usage
-----
The probe reads the router bearer token from either ``$GONKA_API_KEY`` or
``~/.tradingagents/app/settings.json`` (``router_api_key`` field — the
same place the FastAPI server reads it from). SDK / signed-transport
mode is not supported here because parameter-sweep probing makes the
per-request signing latency unjustifiable.

  python scripts/probe_qwen_repetition.py
  python scripts/probe_qwen_repetition.py --samples 5 --output runs/probe1.jsonl

Defaults: 3 samples per config, ~13 configs (baseline + single-knob
sweep + two combinations), JSONL written to ``probe_qwen_<timestamp>.jsonl``
in the current directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Ensure repo root is importable when the script runs from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tradingagents.agents.utils.degeneracy import is_degenerate_text  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QWEN_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
ROUTER_BASE_URL = "https://api.gonkascan.com/v1"


# ---------------------------------------------------------------------------
# Synthetic Market Analyst-style prompt
# ---------------------------------------------------------------------------
#
# Reproduces the *final-report* turn of the Market Analyst node: the model
# has already issued its tool calls, the tool results are stitched into
# the user message as if they came back from the tool runner, and the
# model is asked to write the prose report. This is the turn where the
# observed degeneracy occurs (tool-call turns produce structured function
# arguments, which don't loop in the same way).

_SYSTEM_MESSAGE = """You are a trading assistant tasked with analyzing financial markets. Your role is to summarize the most relevant indicators for a given market condition or trading strategy from the analysis data you have already gathered. Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions. Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.

If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable, prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop.

For your reference, the current date is 2026-05-23. The ticker being analysed is AVGO (Broadcom Inc., a U.S. semiconductor stock listed on Nasdaq; use 'AVGO' exactly when referencing the symbol)."""

_USER_MESSAGE = """Based on the indicator data below for AVGO between 2026-04-23 and 2026-05-23, write a detailed market analysis report. Discuss the trend, momentum, volatility, and volume picture and what each indicator implies for a trader considering a position over the next 1-3 weeks. Conclude with an explicit FINAL TRANSACTION PROPOSAL line.

Selected indicators (8 of 11, complementary across moving averages, MACD, momentum, volatility, and volume):

close_50_sma: 50-day SMA
date,value
2026-04-23,378.42
2026-04-30,381.05
2026-05-07,384.18
2026-05-14,388.91
2026-05-21,393.74

close_200_sma: 200-day SMA
date,value
2026-04-23,341.20
2026-04-30,342.85
2026-05-07,344.51
2026-05-14,346.19
2026-05-21,347.83

close_10_ema: 10-day EMA
date,value
2026-04-23,388.55
2026-04-30,392.10
2026-05-07,395.62
2026-05-14,401.18
2026-05-21,408.92

macd: MACD line
date,value
2026-04-23,3.42
2026-04-30,4.10
2026-05-07,4.85
2026-05-14,5.61
2026-05-21,6.18

macds: MACD signal line
date,value
2026-04-23,2.80
2026-04-30,3.25
2026-05-07,3.80
2026-05-14,4.41
2026-05-21,5.02

macdh: MACD histogram
date,value
2026-04-23,0.62
2026-04-30,0.85
2026-05-07,1.05
2026-05-14,1.20
2026-05-21,1.16

rsi: Relative Strength Index
date,value
2026-04-23,58.4
2026-04-30,62.1
2026-05-07,65.8
2026-05-14,69.3
2026-05-21,72.5

boll_ub: Bollinger upper band (20 SMA + 2*sigma)
date,value
2026-04-23,401.18
2026-04-30,406.74
2026-05-07,412.40
2026-05-14,418.15
2026-05-21,423.92

boll_lb: Bollinger lower band (20 SMA - 2*sigma)
date,value
2026-04-23,361.85
2026-04-30,363.42
2026-05-07,365.10
2026-05-14,366.71
2026-05-21,368.30

atr: Average True Range (14-day)
date,value
2026-04-23,9.42
2026-04-30,10.05
2026-05-07,10.73
2026-05-14,11.40
2026-05-21,12.18

vwma: Volume-weighted moving average (20-day)
date,value
2026-04-23,381.92
2026-04-30,385.04
2026-05-07,388.45
2026-05-14,392.18
2026-05-21,396.81

Use this data to write the report now."""


# ---------------------------------------------------------------------------
# Generation-config sweep
# ---------------------------------------------------------------------------


@dataclass
class GenConfig:
    """One row of the sweep. Maps cleanly onto ChatOpenAI kwargs.

    ``temperature`` / ``top_p`` / ``frequency_penalty`` / ``presence_penalty``
    are passed as native ChatOpenAI kwargs. ``repetition_penalty`` is
    vLLM-specific and ships via ``extra_body`` (langchain-openai forwards
    extra_body verbatim into the chat-completions request body).
    """

    label: str
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None

    def chat_openai_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {}
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        if self.top_p is not None:
            # ChatOpenAI exposes top_p only via model_kwargs (it's not a
            # constructor field in older langchain-openai versions).
            kw.setdefault("model_kwargs", {})["top_p"] = self.top_p
        if self.frequency_penalty is not None:
            kw["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty is not None:
            kw["presence_penalty"] = self.presence_penalty
        if self.repetition_penalty is not None:
            kw["extra_body"] = {"repetition_penalty": self.repetition_penalty}
        return kw


_SWEEP: tuple[GenConfig, ...] = (
    # Baseline: what production currently sends. Nothing tuned.
    GenConfig("baseline (no penalties)"),

    # Single-knob candidates. Temperature/top_p variants were dropped after
    # the first sweep — at temp=0.7 the Gonka executor returned
    # "winner inference incomplete (nonce_finished=false)" on 2/3 trials,
    # which means changing temperature also changes how vLLM races on
    # Gonka's decentralised fleet, costing us the very stability we
    # wanted to add.
    GenConfig("freq_pen=0.3", frequency_penalty=0.3),
    GenConfig("freq_pen=0.6", frequency_penalty=0.6),
    GenConfig("pres_pen=0.3", presence_penalty=0.3),
    GenConfig("pres_pen=0.6", presence_penalty=0.6),
    GenConfig("rep_pen=1.05",  repetition_penalty=1.05),
    GenConfig("rep_pen=1.10",  repetition_penalty=1.10),

    # Combinations — the realistic production-default candidates.
    GenConfig("freq_pen=0.3 + rep_pen=1.05",
              frequency_penalty=0.3, repetition_penalty=1.05),
    GenConfig("freq_pen=0.3 + pres_pen=0.3 + rep_pen=1.05",
              frequency_penalty=0.3, presence_penalty=0.3,
              repetition_penalty=1.05),
)


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------


@dataclass
class TrialResult:
    config_label: str
    config: dict[str, Any]
    sample_index: int
    chars: int
    words: int
    degenerate: bool
    elapsed_s: float
    head: str
    tail: str
    error: str | None
    full: str


def _resolve_router_key() -> str | None:
    """Look in env first, then fall back to the FastAPI server's settings
    file so the probe works without exporting GONKA_API_KEY by hand."""
    api_key = os.environ.get("GONKA_API_KEY")
    if api_key:
        return api_key
    settings_path = Path.home() / ".tradingagents" / "app" / "settings.json"
    if not settings_path.exists():
        return None
    try:
        return (json.loads(settings_path.read_text()).get("router_api_key") or None)
    except (OSError, json.JSONDecodeError):
        return None


def _build_llm(base_url: str, api_key: str, model: str, cfg: GenConfig):
    """Construct a bare ChatOpenAI bound at the Gonka router with cfg
    applied. We bypass GonkaClient because we want to exercise param
    combinations the client doesn't currently forward."""
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "streaming": True,
        "stream_usage": True,
        "max_tokens": 8192,
        "timeout": 600,
    }
    kwargs.update(cfg.chat_openai_kwargs())
    return ChatOpenAI(**kwargs)


def _run_one_trial(llm, sample_index: int, cfg: GenConfig) -> TrialResult:
    """Single fresh request. The user message is salted with a per-trial
    nonce so Gonka's router-side prompt+params cache cannot hand us the
    same completion N times in a row (observed: identical chars/words and
    2-3s latency for samples 2..N of every config in the first sweep —
    proof the router caches by exact prompt+params key)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    import uuid

    nonce = uuid.uuid4().hex
    cache_buster = (
        f"\n\n(internal trace tag, ignore in analysis: trial={sample_index} nonce={nonce})"
    )
    messages = [
        SystemMessage(content=_SYSTEM_MESSAGE),
        HumanMessage(content=_USER_MESSAGE + cache_buster),
    ]
    started = time.monotonic()
    try:
        result = llm.invoke(messages)
        content = result.content if hasattr(result, "content") else str(result)
    except Exception as exc:  # noqa: BLE001 — record and move on
        elapsed = time.monotonic() - started
        return TrialResult(
            config_label=cfg.label,
            config=cfg.chat_openai_kwargs(),
            sample_index=sample_index,
            chars=0,
            words=0,
            degenerate=False,
            elapsed_s=elapsed,
            head="",
            tail="",
            error=f"{type(exc).__name__}: {exc}",
            full="",
        )

    elapsed = time.monotonic() - started
    text = content if isinstance(content, str) else str(content)
    words = len(text.split())
    return TrialResult(
        config_label=cfg.label,
        config=cfg.chat_openai_kwargs(),
        sample_index=sample_index,
        chars=len(text),
        words=words,
        degenerate=is_degenerate_text(text),
        elapsed_s=elapsed,
        head=text[:160].replace("\n", " ⏎ "),
        tail=text[-160:].replace("\n", " ⏎ ") if len(text) > 160 else "",
        error=None,
        full=text,
    )


def _print_summary(rows: list[TrialResult], samples_per_config: int) -> None:
    # Group by config_label and aggregate.
    by_cfg: dict[str, list[TrialResult]] = {}
    for r in rows:
        by_cfg.setdefault(r.config_label, []).append(r)

    print()
    print("=" * 96)
    print(f"{'config':<48}{'N':>3}  {'deg':>4}  {'err':>4}  {'mean_chars':>11}  {'mean_s':>8}")
    print("=" * 96)
    for label, trials in by_cfg.items():
        n = len(trials)
        deg = sum(1 for t in trials if t.degenerate)
        err = sum(1 for t in trials if t.error is not None)
        clean = [t for t in trials if t.error is None]
        mean_chars = (sum(t.chars for t in clean) / len(clean)) if clean else 0.0
        mean_s = (sum(t.elapsed_s for t in clean) / len(clean)) if clean else 0.0
        print(
            f"{label:<48}{n:>3}  {deg:>4}  {err:>4}  {mean_chars:>11.0f}  {mean_s:>8.1f}"
        )
    print("=" * 96)
    print(f"Goal: minimise 'deg' (degenerate samples out of {samples_per_config}).")
    print("Lower mean_chars at near-zero deg = model is being tighter, not collapsing.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default=QWEN_MODEL,
        help=f"Model id (default: {QWEN_MODEL})",
    )
    parser.add_argument(
        "--base-url", default=ROUTER_BASE_URL,
        help=f"Gonka endpoint (default: {ROUTER_BASE_URL})",
    )
    parser.add_argument(
        "--samples", type=int, default=3,
        help="Trials per config (default 3; raise to 5-10 once a candidate is shortlisted)",
    )
    parser.add_argument(
        "--only", metavar="LABEL_SUBSTR", default=None,
        help="Run only configs whose label contains this substring (debugging shortcut)",
    )
    parser.add_argument(
        "--output", default=None,
        help="JSONL output path (default: probe_qwen_<timestamp>.jsonl)",
    )
    args = parser.parse_args()

    api_key = _resolve_router_key()
    if not api_key:
        print(
            "No router bearer token found. Set GONKA_API_KEY in env, or store "
            "`router_api_key` in ~/.tradingagents/app/settings.json (the same "
            "place the FastAPI server reads it from).",
            file=sys.stderr,
        )
        return 2

    configs = list(_SWEEP)
    if args.only:
        configs = [c for c in configs if args.only in c.label]
        if not configs:
            print(f"No config matches --only={args.only!r}", file=sys.stderr)
            return 2

    out_path = Path(args.output or f"probe_qwen_{int(time.time())}.jsonl")
    print(f"Writing trials to {out_path}")
    print(f"Model={args.model}  base_url={args.base_url}")
    print(f"Configs: {len(configs)}  samples/config: {args.samples}  "
          f"total trials: {len(configs) * args.samples}")
    print()

    all_rows: list[TrialResult] = []
    with out_path.open("w", encoding="utf-8") as f:
        for cfg in configs:
            llm = _build_llm(args.base_url, api_key, args.model, cfg)
            for i in range(args.samples):
                tag = f"[{cfg.label}] {i+1}/{args.samples}"
                print(tag, "→", flush=True, end=" ")
                row = _run_one_trial(llm, i, cfg)
                all_rows.append(row)
                f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
                f.flush()
                if row.error:
                    print(f"ERROR  {row.error}")
                else:
                    flag = "DEG " if row.degenerate else "ok  "
                    print(
                        f"{flag} chars={row.chars:>5}  words={row.words:>4}  "
                        f"{row.elapsed_s:>5.1f}s  head={row.head[:80]!r}"
                    )

    _print_summary(all_rows, args.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
