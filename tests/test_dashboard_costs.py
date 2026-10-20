"""Dashboard shows only the bot's own trades, with every cost; the live position view never exposes a
position the bot didn't open. No network: the exchange is faked."""

import pytest

import dashboard
from shark.bot_registry import BotRegistry
from shark.exchange import ExchangeClient
from shark.performance import PerformanceTracker
from shark.position_view import describe_open_position

SYM = "SOLUSDT"


@pytest.fixture
def csv_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    return tmp_path


def _write_trades(csv_dir):
    t = PerformanceTracker(str(csv_dir / "shark_bot_trade_log_psar_trend.csv"), 5000.0, SYM)
    t.record(100, 101, 1, "tp", 10.0, 1.0, extra={"side": "LONG", "source": "simulated"})
    t.record(105.07, 107.0, 0.24, "sar_flip", 47.26, 2.52, discount=0.25, funding=-0.59,
             extra={"side": "LONG", "source": "settled", "entry_role": "TAKER", "exit_role": "TAKER"})
    t.record(110, 109, 0.24, "closed_on_exchange_estimated", -5.0, 0.3, extra={"side": "LONG", "source": "estimated"})


def test_real_mode_shows_only_real_trades_with_every_cost(csv_dir):
    _write_trades(csv_dir)
    trades = dashboard.read_trades(dry_run=False)
    assert [t["source"] for t in trades] == ["settled", "estimated"]      # the simulated one is hidden
    settled = trades[0]
    assert (settled["gross"], settled["fees"], settled["discount"], settled["funding"]) == (47.26, 2.52, 0.25, -0.59)
    assert settled["net"] == pytest.approx(44.40) and settled["entry_role"] == "TAKER"


def test_dry_run_mode_shows_only_simulated_trades(csv_dir):
    _write_trades(csv_dir)
    assert [t["source"] for t in dashboard.read_trades(dry_run=True)] == ["simulated"]


def test_stats_total_every_cost_and_flag_estimates(csv_dir):
    _write_trades(csv_dir)
    stats, _ = dashboard.compute_stats(dashboard.read_trades(dry_run=False), 5000.0)
    assert stats["gross"] == pytest.approx(47.26 - 5.0)
    assert stats["fees"] == pytest.approx(2.52 + 0.3)
    assert stats["discounts"] == pytest.approx(0.25) and stats["funding"] == pytest.approx(-0.59)
    assert stats["net"] == pytest.approx(44.40 - 5.3)
    assert stats["equity"] == pytest.approx(5000 + 44.40 - 5.3)             # gross - fees + discounts + funding
    assert stats["estimated_trades"] == 1


def test_older_rows_without_cost_columns_still_load(csv_dir):
    (csv_dir / "shark_bot_trade_log_x.csv").write_text(
        "timestamp,symbol,entry_price,exit_price,qty,exit_reason,gross_pnl,fees,net_pnl,equity_after\n"
        "2026-10-01T10:00:00,SOLUSDT,100,101,1,tp,1.0,0.1,0.9,5000.9\n")
    (t,) = dashboard.read_trades(dry_run=True)                              # legacy rows count as dry-run era
    assert t["discount"] == 0.0 and t["funding"] == 0.0 and t["source"] == "legacy"


class FakeEx(ExchangeClient):
    def __init__(self):
        super().__init__("https://fake", "k", "s")
        self.position = {"contractPair": SYM, "positionType": "LONG", "positionAmount": 0.24, "quantity": 0.24,
                         "entryPrice": 100.0, "positionStatus": "OPEN", "positionId": "p1",
                         "createdAt": "2026-10-20T13:58:56.942Z", "liquidationPrice": 90.0, "leverage": 20,
                         "marginInMarginAsset": 128.68, "marginSettlementRate": 100}
        self.trades = [{"id": 1, "positionId": "p1", "clientOrderId": "bot-1", "side": "BUY", "quantity": 0.24,
                        "price": 100.0, "feeInMarginAsset": 1.22, "time": "2026-10-20T13:58:56.927Z"}]
        self.ledger = [
            {"time": "2026-10-20T13:58:57.000Z", "type": "FEE_DISCOUNT", "amount": 0.12, "tradeId": None},
            {"time": "2026-10-21T00:00:00.700Z", "type": "FUNDING_FEE", "amount": -0.5, "tradeId": None},
            {"time": "2026-10-19T00:00:00.700Z", "type": "FUNDING_FEE", "amount": -9.0, "tradeId": None},  # before open
        ]
        self.orders = [{"clientOrderId": "bot-stop", "symbol": SYM, "type": "STOP_MARKET", "stopPrice": 98.0},
                       {"clientOrderId": "manual", "symbol": SYM, "type": "STOP_MARKET", "stopPrice": 50.0}]

    def get_positions(self, status, symbol=None): return [self.position]
    def get_trade_history(self, symbol, start_ms=None, page_size=200): return self.trades
    def get_transaction_history(self, symbol, start_ms=None, page_size=200): return self.ledger
    def list_open_orders(self, symbol): return self.orders
    def get_last_price(self, symbol): return 102.0


def test_position_view_shows_costs_for_the_bots_own_position():
    reg = BotRegistry(None); reg.add_order("bot-1"); reg.add_order("bot-stop")
    v = describe_open_position(FakeEx(), reg, SYM, taker_fee_pct_eff=0.0472)
    assert v["open"] and v["side"] == "LONG"
    assert v["unrealised_inr"] == pytest.approx(2.0 * 0.24 * 100)           # uses Shark's own conversion rate
    assert (v["entry_fee_inr"], v["entry_discount_inr"], v["funding_inr"]) == pytest.approx((1.22, 0.12, -0.5))
    assert v["est_exit_fee_inr"] == pytest.approx(102.0 * 0.24 * 100 * 0.0472 / 100)
    assert v["net_if_closed_now_inr"] == pytest.approx(48.0 - 1.22 + 0.12 - 0.5 - v["est_exit_fee_inr"])
    assert v["stop_price"] == 98.0 and v["stop_on_exchange"]                # OUR stop, not the manual one at 50


def test_position_view_never_describes_a_position_the_bot_did_not_open():
    v = describe_open_position(FakeEx(), BotRegistry(None), SYM, taker_fee_pct_eff=0.0472)
    assert v == {"open": False, "foreign_open": True}                       # no price, size or stop leaked
