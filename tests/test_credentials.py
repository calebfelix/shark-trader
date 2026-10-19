import json

import pytest

from shark import credentials
from shark.config import load_config


@pytest.fixture(autouse=True)
def isolated_credentials_file(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("SHARK_CREDENTIALS_FILE", str(path))
    monkeypatch.delenv("SHARK_API_KEY", raising=False)
    monkeypatch.delenv("SHARK_API_SECRET", raising=False)
    return path


class TestCredentialsFile:
    def test_missing_file_returns_empty(self):
        assert credentials.get_credentials() == ("", "")

    def test_roundtrip(self, isolated_credentials_file):
        credentials.set_credentials("key-12345678", "secret-12345678")
        assert credentials.get_credentials() == ("key-12345678", "secret-12345678")
        assert json.loads(isolated_credentials_file.read_text())["api_key"] == "key-12345678"

    def test_corrupt_file_returns_empty(self, isolated_credentials_file):
        isolated_credentials_file.write_text("not json")
        assert credentials.get_credentials() == ("", "")

    def test_clear_removes_file_and_is_idempotent(self, isolated_credentials_file):
        credentials.set_credentials("key-12345678", "secret-12345678")
        credentials.clear_credentials()
        credentials.clear_credentials()
        assert not isolated_credentials_file.exists()

    def test_key_hint_shows_only_last_four(self):
        assert credentials.key_hint("abcdefgh1234") == "…1234"
        assert credentials.key_hint("short") == ""


class TestConfigPrecedence:
    def test_saved_file_beats_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SHARK_API_KEY", "env-key")
        monkeypatch.setenv("SHARK_API_SECRET", "env-secret")
        credentials.set_credentials("file-key-1234", "file-secret-1234")
        cfg = load_config()
        assert (cfg["api_key"], cfg["api_secret"]) == ("file-key-1234", "file-secret-1234")

    def test_env_used_when_no_file(self, monkeypatch):
        monkeypatch.setenv("SHARK_API_KEY", "env-key")
        monkeypatch.setenv("SHARK_API_SECRET", "env-secret")
        cfg = load_config()
        assert (cfg["api_key"], cfg["api_secret"]) == ("env-key", "env-secret")

    def test_credentials_never_in_config_json(self):
        text = open("config.json", encoding="utf-8").read().lower()
        assert "api_key" not in text and "api_secret" not in text


def test_credentials_file_is_gitignored():
    ignored = open(".gitignore", encoding="utf-8").read().splitlines()
    assert "credentials.json" in ignored
