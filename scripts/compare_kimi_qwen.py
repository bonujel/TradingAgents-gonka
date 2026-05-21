"""Standalone Kimi-K2.6 vs Qwen3 sanity probe (no project code touched).

Goal: figure out whether Kimi really returns empty completions for the
prompts that have been failing in the multi-agent pipeline, and what
finish_reason / usage look like vs Qwen on identical input.

Run:  python scripts/compare_kimi_qwen.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


API_KEY = os.environ["GONKA_API_KEY"]
BASE = "https://api.gonkascan.com/v1"

MODELS = [
    "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
    "moonshotai/Kimi-K2.6",
]

# Three prompts of increasing TradingAgents-ish complexity.
PROMPTS = [
    # 1. trivial — both should answer
    {"role": "user", "content": "Reply with exactly one word: hello"},
    # 2. medium — short stock analysis (similar to News Analyst's first call)
    {
        "role": "user",
        "content": (
            "Briefly summarise the bull and bear case for Booking Holdings (BKNG) "
            "in 4–6 sentences. Mention valuation, growth, and key risks."
        ),
    },
    # 3. heavy — long structured ask (similar to Research Manager / Portfolio Manager)
    {
        "role": "user",
        "content": (
            "You are a senior portfolio manager. Given that the market analyst "
            "is mildly bullish on Accenture (ACN) citing AI-services tailwinds "
            "and 18× forward P/E, while the bear analyst flags slowing IT spend "
            "and consultant headcount cuts, write a 250–400 word investment "
            "memo with: (a) recommended rating (Buy/Hold/Sell), (b) one-month "
            "price target with rationale, (c) three downside risks. Be explicit "
            "and concrete; do not hedge."
        ),
    },
]


def run_one(model: str, prompt: dict, *, stream: bool) -> dict:
    body = {
        "model": model,
        "messages": [prompt],
        "stream": stream,
        "temperature": 0.7,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}

    started = time.monotonic()
    url = f"{BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    if not stream:
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=300)
        except Exception as exc:
            return {"err": f"transport: {type(exc).__name__}: {exc}",
                    "elapsed": time.monotonic() - started}
        if resp.status_code != 200:
            return {"err": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "elapsed": time.monotonic() - started}
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return {
            "elapsed": time.monotonic() - started,
            "finish_reason": choice.get("finish_reason"),
            "content_len": len(((choice.get("message") or {}).get("content") or "")),
            "content_head": ((choice.get("message") or {}).get("content") or "")[:160],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            ),
        }

    # streaming branch
    chunks_seen = 0
    delta_tokens = 0
    finish_reason = None
    usage = None
    text = ""
    try:
        with httpx.stream("POST", url, json=body, headers=headers, timeout=300) as r:
            if r.status_code != 200:
                return {"err": f"HTTP {r.status_code}",
                        "elapsed": time.monotonic() - started}
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = __import__("json").loads(payload)
                except Exception:
                    continue
                chunks_seen += 1
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if choices:
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if delta:
                        delta_tokens += 1
                        text += delta
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr
    except Exception as exc:
        return {"err": f"stream: {type(exc).__name__}: {exc}",
                "elapsed": time.monotonic() - started,
                "chunks_seen": chunks_seen}

    return {
        "elapsed": time.monotonic() - started,
        "chunks_seen": chunks_seen,
        "content_delta_chunks": delta_tokens,
        "finish_reason": finish_reason,
        "content_len": len(text),
        "content_head": text[:160],
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "reasoning_tokens": ((usage or {}).get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        ),
    }


def main() -> int:
    for prompt_idx, prompt in enumerate(PROMPTS, 1):
        print()
        print("=" * 88)
        print(f"PROMPT {prompt_idx}: {prompt['content'][:90]}…")
        print("=" * 88)
        for model in MODELS:
            for stream in (False, True):
                tag = "stream " if stream else "non-stream"
                short_model = model.split("/", 1)[-1]
                print(f"\n--- {short_model:36s} [{tag}] ---")
                r = run_one(model, prompt, stream=stream)
                if "err" in r:
                    print(f"  ERROR ({r['elapsed']:.1f}s): {r['err']}")
                    continue
                print(f"  elapsed         : {r['elapsed']:.2f}s")
                if "chunks_seen" in r:
                    print(f"  chunks_seen     : {r['chunks_seen']}")
                    print(f"  delta chunks    : {r.get('content_delta_chunks')}")
                print(f"  finish_reason   : {r.get('finish_reason')!r}")
                print(f"  prompt_tokens   : {r.get('prompt_tokens')}")
                print(f"  completion_tok  : {r.get('completion_tokens')}")
                print(f"  reasoning_tok   : {r.get('reasoning_tokens')}")
                print(f"  content_len     : {r.get('content_len')}")
                head = r.get("content_head") or ""
                head = head.replace("\n", "\\n")
                print(f"  content_head    : {head!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
