"""Settings persistence for the operator UI.

Extracted from the original Streamlit dashboard so the FastAPI backend and
any future client can share the same on-disk layout and env-derivation
rules.

Layout::

    ~/.tradingagents/app/settings.json

The file is written atomically (temp + rename) to avoid partial writes on
concurrent saves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_HOME = Path.home() / ".tradingagents" / "app"
SETTINGS_PATH = APP_HOME / "settings.json"

QWEN_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
KIMI_MODEL = "moonshotai/Kimi-K2.6"
MODEL_OPTIONS = (QWEN_MODEL, KIMI_MODEL)

# Per-family completion-token caps. Qwen3-FP8 on Gonka has a serving-side
# degeneracy bug at certain max_completion_tokens values (notably 256, 2048,
# 4000, 8000, 8192) where it emits byte-level garbage instead of stopping.
# 4096 is the empirically-safest value tested 2026-05-23; Kimi-K2.6 still
# wants the 8192 headroom for its reasoning + visible answer.
QWEN_DEFAULT_MAX_TOKENS = 4096
KIMI_DEFAULT_MAX_TOKENS = 8192


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def defaults_from_env() -> dict[str, Any]:
    return {
        "mode": "router" if os.environ.get("GONKA_API_KEY") else "sdk",
        "router_api_key": os.environ.get("GONKA_API_KEY", ""),
        "sdk_private_key": os.environ.get("GONKA_PRIVATE_KEY", ""),
        "sdk_source_url": os.environ.get("GONKA_SOURCE_URL", "https://node4.gonka.ai"),
        "deep_model": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", QWEN_MODEL),
        "quick_model": os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", QWEN_MODEL),
        "max_workers": int(os.environ.get("TRADINGAGENTS_APP_MAX_WORKERS", "4")),
        "qwen_max_tokens": int(
            os.environ.get("TRADINGAGENTS_QWEN_MAX_TOKENS", str(QWEN_DEFAULT_MAX_TOKENS))
        ),
        "kimi_max_tokens": int(
            os.environ.get("TRADINGAGENTS_KIMI_MAX_TOKENS", str(KIMI_DEFAULT_MAX_TOKENS))
        ),
        # When True, every chat-model invocation gets logged to
        # ~/.tradingagents/app/logs/llm_debug.jsonl by the callback in
        # tradingagents/llm_clients/debug_logging.py. Off by default
        # because the file grows ~50-200 KB per ticker run and the prompt
        # bodies are large.
        "llm_debug": _env_truthy("TRADINGAGENTS_LLM_DEBUG"),
        # When True, send ``chat_template_kwargs.thinking=false`` to Gonka
        # for Kimi-family models so the backend skips reasoning_content.
        # Verified 2026-05-25 to cut completion_tokens ~75% and let the
        # full token budget go to the visible answer. Off by default to
        # preserve current behaviour; flip on for faster / cheaper runs
        # where CoT-quality dependence has been validated.
        "disable_kimi_thinking": _env_truthy("TRADINGAGENTS_DISABLE_KIMI_THINKING"),
    }


def load_settings() -> dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text())
            return {**defaults_from_env(), **stored}
        except json.JSONDecodeError:
            pass
    return defaults_from_env()


def save_settings(settings: dict[str, Any]) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(SETTINGS_PATH)


def settings_to_env(settings: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    # Set unwanted Gonka credentials to "" rather than pop()'ing them.
    # tradingagents/__init__.py calls load_dotenv(override=False) at import
    # time, which resurrects any *unset* variable from the project .env file.
    # Popping → variable is unset → dotenv re-loads it from .env → router
    # mode subprocesses silently inherit SDK credentials. Setting to "" keeps
    # the slot occupied so override=False sees it and skips, and downstream
    # callers (gonka_client._sdk_private_key etc.) treat "" as falsy.
    for key in ("GONKA_API_KEY", "GONKA_PRIVATE_KEY", "GONKA_SOURCE_URL"):
        env[key] = ""
    if settings["mode"] == "router" and settings.get("router_api_key"):
        env["GONKA_API_KEY"] = settings["router_api_key"]
    elif settings["mode"] == "sdk":
        if settings.get("sdk_private_key"):
            env["GONKA_PRIVATE_KEY"] = settings["sdk_private_key"]
        if settings.get("sdk_source_url"):
            env["GONKA_SOURCE_URL"] = settings["sdk_source_url"]
    env["TRADINGAGENTS_LLM_PROVIDER"] = "gonka"
    env["TRADINGAGENTS_DEEP_THINK_LLM"] = settings.get("deep_model") or QWEN_MODEL
    env["TRADINGAGENTS_QUICK_THINK_LLM"] = settings.get("quick_model") or QWEN_MODEL
    env["TRADINGAGENTS_APP_MAX_WORKERS"] = str(settings.get("max_workers", 4))
    env["TRADINGAGENTS_QWEN_MAX_TOKENS"] = str(
        settings.get("qwen_max_tokens", QWEN_DEFAULT_MAX_TOKENS)
    )
    env["TRADINGAGENTS_KIMI_MAX_TOKENS"] = str(
        settings.get("kimi_max_tokens", KIMI_DEFAULT_MAX_TOKENS)
    )
    env["TRADINGAGENTS_LLM_DEBUG"] = "1" if settings.get("llm_debug") else "0"
    env["TRADINGAGENTS_DISABLE_KIMI_THINKING"] = (
        "1" if settings.get("disable_kimi_thinking") else "0"
    )
    env["PYTHONUNBUFFERED"] = "1"
    return env


def mode_is_configured(settings: dict[str, Any]) -> bool:
    if settings["mode"] == "router":
        return bool(settings.get("router_api_key"))
    return bool(settings.get("sdk_private_key")) and bool(settings.get("sdk_source_url"))


def redact_secret(value: str, *, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep + 4:
        return "•" * len(value)
    return value[:keep] + "•" * (len(value) - keep - 4) + value[-4:]


def public_view(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings safe to ship to the browser — secrets replaced by a flag.

    The frontend doesn't need the actual key material to render the form; it
    only needs to know whether each field is populated so it can mark them as
    "stored" and surface the right validation state.
    """
    return {
        "mode": settings["mode"],
        "router_api_key_set": bool(settings.get("router_api_key")),
        "router_api_key_preview": redact_secret(settings.get("router_api_key", "")),
        "sdk_private_key_set": bool(settings.get("sdk_private_key")),
        "sdk_private_key_preview": redact_secret(settings.get("sdk_private_key", "")),
        "sdk_source_url": settings.get("sdk_source_url", ""),
        "deep_model": settings.get("deep_model"),
        "quick_model": settings.get("quick_model"),
        "max_workers": settings.get("max_workers", 4),
        "qwen_max_tokens": settings.get("qwen_max_tokens", QWEN_DEFAULT_MAX_TOKENS),
        "kimi_max_tokens": settings.get("kimi_max_tokens", KIMI_DEFAULT_MAX_TOKENS),
        "llm_debug": bool(settings.get("llm_debug", False)),
        "disable_kimi_thinking": bool(settings.get("disable_kimi_thinking", False)),
        "configured": mode_is_configured(settings),
    }
