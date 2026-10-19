"""Trading-mode (dry run / real account) gates. Nothing here launches a
bot or talks to the exchange: Popen is faked and every settings file lives
in a temp directory."""

import pytest

import dashboard
from shark import credentials, local_settings
from shark.config import load_config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARK_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("SHARK_LOCAL_SETTINGS_FILE", str(tmp_path / "local_settings.json"))
    monkeypatch.delenv("SHARK_API_KEY", raising=False)
    monkeypatch.delenv("SHARK_API_SECRET", raising=False)
    monkeypatch.setattr(dashboard, "LOG_PATH", tmp_path / "bot.log")
    monkeypatch.setattr(dashboard, "find_bot_pids", lambda: [])
    launched = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *a, **k: launched.append(a))
    return launched


def _save_keys():
    credentials.set_credentials("key-12345678", "secret-12345678")


class TestModeOverride:
    def test_default_is_config_json_value(self):
        assert load_config()["dry_run"] is True

    def test_saved_override_wins(self):
        local_settings.set_dry_run(False)
        assert load_config()["dry_run"] is False
        local_settings.set_dry_run(True)
        assert load_config()["dry_run"] is True

    def test_garbage_override_ignored(self, tmp_path):
        (tmp_path / "local_settings.json").write_text('{"dry_run": "no"}')
        assert load_config()["dry_run"] is True


class TestSwitchingToLive:
    def test_refused_without_credentials(self):
        r = dashboard.set_mode(dashboard.ModeRequest(mode="live", confirm="REAL MONEY"))
        assert r.status_code == 409
        assert load_config()["dry_run"] is True

    def test_refused_without_exact_phrase(self):
        _save_keys()
        for phrase in ("", "yes", "real money please"):
            r = dashboard.set_mode(dashboard.ModeRequest(mode="live", confirm=phrase))
            assert r.status_code == 422
        assert load_config()["dry_run"] is True

    def test_allowed_with_credentials_and_phrase(self):
        _save_keys()
        r = dashboard.set_mode(dashboard.ModeRequest(mode="live", confirm="REAL MONEY"))
        assert r["ok"] is True
        assert load_config()["dry_run"] is False

    def test_switching_back_to_dry_run_needs_no_confirmation(self):
        _save_keys()
        dashboard.set_mode(dashboard.ModeRequest(mode="live", confirm="REAL MONEY"))
        r = dashboard.set_mode(dashboard.ModeRequest(mode="dry_run"))
        assert r["ok"] is True
        assert load_config()["dry_run"] is True

    def test_refused_while_a_bot_is_running(self, monkeypatch):
        _save_keys()
        monkeypatch.setattr(dashboard, "find_bot_pids", lambda: [123])
        r = dashboard.set_mode(dashboard.ModeRequest(mode="live", confirm="REAL MONEY"))
        assert r.status_code == 409
        assert load_config()["dry_run"] is True


class TestStartGate:
    def test_dry_run_starts_without_confirmation(self, isolated):
        ok, _ = dashboard.start_bot("psar_trend")
        assert ok and len(isolated) == 1

    def test_live_start_needs_confirmation(self, isolated):
        _save_keys()
        local_settings.set_dry_run(False)
        ok, msg = dashboard.start_bot("psar_trend", confirm_live=False)
        assert not ok and "confirmation" in msg
        assert isolated == []

    def test_live_start_needs_credentials(self, isolated):
        local_settings.set_dry_run(False)
        ok, msg = dashboard.start_bot("psar_trend", confirm_live=True)
        assert not ok and "credentials" in msg
        assert isolated == []

    def test_live_start_with_credentials_and_confirmation(self, isolated):
        _save_keys()
        local_settings.set_dry_run(False)
        ok, msg = dashboard.start_bot("psar_trend", confirm_live=True)
        assert ok and "LIVE" in msg
        assert len(isolated) == 1


def test_local_settings_file_is_gitignored():
    ignored = open(".gitignore", encoding="utf-8").read().splitlines()
    assert "local_settings.json" in ignored
