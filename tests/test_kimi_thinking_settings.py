"""Settings round-trip tests for the Kimi-thinking toggle.

Mirrors the pattern of tests/test_llm_debug_logging.py's settings-roundtrip
suite. The toggle's runtime effect on the Gonka payload is covered in
tests/test_gonka_client.py::TestKimiThinkingToggle; this file covers the
persistence + env-export plumbing.
"""

from __future__ import annotations

import pytest


def _base() -> dict:
    return {
        "mode": "router",
        "router_api_key": "k",
        "sdk_private_key": "",
        "sdk_source_url": "",
        "deep_model": "m",
        "quick_model": "m",
        "max_workers": 4,
    }


@pytest.mark.unit
class TestKimiThinkingSettings:
    def test_settings_to_env_emits_truthy_flag(self):
        from app.settings_store import settings_to_env

        env = settings_to_env({**_base(), "disable_kimi_thinking": True})
        assert env["TRADINGAGENTS_DISABLE_KIMI_THINKING"] == "1"

    def test_settings_to_env_emits_falsy_when_disabled(self):
        from app.settings_store import settings_to_env

        env = settings_to_env({**_base(), "disable_kimi_thinking": False})
        assert env["TRADINGAGENTS_DISABLE_KIMI_THINKING"] == "0"

    def test_settings_to_env_treats_missing_as_off(self):
        """A stored settings file written before this field existed must
        round-trip as off, never as None / unset (the env var contract is
        always present, value is "0" or "1")."""
        from app.settings_store import settings_to_env

        env = settings_to_env(_base())
        assert env["TRADINGAGENTS_DISABLE_KIMI_THINKING"] == "0"

    def test_public_view_exposes_flag(self):
        from app.settings_store import public_view

        view = public_view({**_base(), "disable_kimi_thinking": True})
        assert view["disable_kimi_thinking"] is True

    def test_public_view_defaults_to_false_when_missing(self):
        from app.settings_store import public_view

        view = public_view(_base())
        assert view["disable_kimi_thinking"] is False

    def test_default_from_env_reads_truthy(self, monkeypatch):
        from app.settings_store import defaults_from_env

        monkeypatch.setenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", "true")
        assert defaults_from_env()["disable_kimi_thinking"] is True

    def test_default_from_env_reads_falsy(self, monkeypatch):
        from app.settings_store import defaults_from_env

        monkeypatch.delenv("TRADINGAGENTS_DISABLE_KIMI_THINKING", raising=False)
        assert defaults_from_env()["disable_kimi_thinking"] is False

    def test_put_settings_persists_and_returns_in_view(self, monkeypatch, tmp_path):
        """End-to-end: PUT /api/settings round-trips the field through
        SettingsUpdate → settings.json → public_view."""
        import app.settings_store as store
        from app.api import put_settings, SettingsUpdate

        monkeypatch.setattr(store, "APP_HOME", tmp_path)
        monkeypatch.setattr(store, "SETTINGS_PATH", tmp_path / "settings.json")

        update = SettingsUpdate(
            mode="router",
            router_api_key="k",
            deep_model="m",
            quick_model="m",
            max_workers=4,
            qwen_max_tokens=4096,
            kimi_max_tokens=8192,
            llm_debug=False,
            disable_kimi_thinking=True,
        )
        view = put_settings(update)
        assert view["disable_kimi_thinking"] is True

        # Re-load from disk to confirm persistence.
        reloaded = store.load_settings()
        assert reloaded["disable_kimi_thinking"] is True
