"""Real-wallet balance: response parsing and the dry-run/live gating. No test
here makes a network call - signed_get and ExchangeClient are faked."""

import pytest

import dashboard
from shark import credentials, local_settings
from shark.exchange import ExchangeClient

# Example response from docs.sharkexchange.in (GET /v1/wallet/futures-wallet/details)
DOCS_EXAMPLE = {
    "inrBalance": "1941.50", "walletBalance": "1941.50", "withdrawableBalance": "1468.50",
    "maintenanceMargin": "0.00", "unrealisedPnlCross": "1.25", "unrealisedPnlIsolated": "-0.25",
    "maxWithdrawableBalance": "1468.50", "lockedBalance": "473.00", "marginBalance": "1468.52",
    "pnlPercentCross": "0.00", "pnlPercentIsolated": "0.00", "lockedBalanceCross": "0.00",
    "lockedBalanceIsolated": "473.00", "marginAsset": "INR",
}


class TestGetFuturesWallet:
    def _client(self, monkeypatch, response):
        client = ExchangeClient("https://example.test", "k", "s")
        monkeypatch.setattr(client, "signed_get", lambda endpoint, params=None: response)
        return client

    def test_parses_docs_example(self, monkeypatch):
        w = self._client(monkeypatch, DOCS_EXAMPLE).get_futures_wallet()
        assert w["wallet_balance"] == 1941.50
        assert w["withdrawable"] == 1468.50
        assert w["locked"] == 473.00
        assert w["unrealised_pnl"] == pytest.approx(1.0)
        assert w["asset"] == "INR"

    def test_unwraps_data_envelope(self, monkeypatch):
        w = self._client(monkeypatch, {"data": DOCS_EXAMPLE}).get_futures_wallet()
        assert w["wallet_balance"] == 1941.50

    def test_unexpected_shape_raises_instead_of_showing_zero(self, monkeypatch):
        with pytest.raises(ValueError):
            self._client(monkeypatch, {"error": "nope"}).get_futures_wallet()

    def test_uses_documented_endpoint_and_margin_asset(self, monkeypatch):
        seen = {}
        client = ExchangeClient("https://example.test", "k", "s")

        def fake(endpoint, params=None):
            seen["endpoint"], seen["params"] = endpoint, params
            return DOCS_EXAMPLE

        monkeypatch.setattr(client, "signed_get", fake)
        client.get_futures_wallet()
        assert seen["endpoint"] == "/v1/wallet/futures-wallet/details"
        assert seen["params"] == {"marginAsset": "INR"}


class TestFetchBalanceGating:
    @pytest.fixture(autouse=True)
    def isolated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHARK_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
        monkeypatch.setenv("SHARK_LOCAL_SETTINGS_FILE", str(tmp_path / "local_settings.json"))
        monkeypatch.delenv("SHARK_API_KEY", raising=False)
        monkeypatch.delenv("SHARK_API_SECRET", raising=False)
        dashboard._balance_cache.update(at=0.0, value=None)
        self.calls = []

        class FakeClient:
            def __init__(inner, *a, **k):
                self.calls.append("constructed")

            def get_futures_wallet(inner, asset="INR"):
                self.calls.append("wallet")
                return {"wallet_balance": 100.0, "withdrawable": 90.0, "locked": 10.0,
                        "margin_balance": 100.0, "unrealised_pnl": 0.0, "asset": "INR"}

        monkeypatch.setattr(dashboard, "ExchangeClient", FakeClient)

    def test_dry_run_never_touches_the_exchange(self):
        credentials.set_credentials("key-12345678", "secret-12345678")
        r = dashboard.fetch_balance()
        assert r["ok"] is False and r.get("dry_run") is True
        assert self.calls == []

    def test_live_without_credentials_reports_error(self):
        local_settings.set_dry_run(False)
        r = dashboard.fetch_balance()
        assert r["ok"] is False and "credentials" in r["error"].lower()
        assert self.calls == []

    def test_live_with_credentials_returns_wallet_and_caches(self):
        credentials.set_credentials("key-12345678", "secret-12345678")
        local_settings.set_dry_run(False)
        first = dashboard.fetch_balance()
        second = dashboard.fetch_balance()
        assert first["ok"] is True and first["wallet_balance"] == 100.0
        assert second == first
        assert self.calls.count("wallet") == 1

    def test_exchange_error_is_reported_not_raised(self, monkeypatch):
        credentials.set_credentials("key-12345678", "secret-12345678")
        local_settings.set_dry_run(False)

        class Boom:
            def __init__(self, *a, **k):
                pass

            def get_futures_wallet(self, asset="INR"):
                raise RuntimeError("503 Service Unavailable")

        monkeypatch.setattr(dashboard, "ExchangeClient", Boom)
        r = dashboard.fetch_balance()
        assert r["ok"] is False and "503" in r["error"]
