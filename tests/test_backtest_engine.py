from typing import Optional

from shark import backtest_engine as bt
from shark.strategies.base import EntryPlan, Position, Strategy

from .conftest import make_candles


class TestTradeClose:
    def test_long_pnl_sign(self):
        trade = bt.Trade(entry_price=100.0, qty=1.0, entry_notional=100.0,
                          entry_is_maker=True, side="LONG")
        trade.close(exit_price=110.0, exit_reason="take_profit", exit_is_maker=True,
                    maker_fee_pct=0.0, taker_fee_pct=0.0)
        assert trade.gross_pnl == 10.0

    def test_short_pnl_sign(self):
        trade = bt.Trade(entry_price=100.0, qty=1.0, entry_notional=100.0,
                          entry_is_maker=True, side="SHORT")
        trade.close(exit_price=90.0, exit_reason="take_profit", exit_is_maker=True,
                    maker_fee_pct=0.0, taker_fee_pct=0.0)
        assert trade.gross_pnl == 10.0  # short profits when price falls

    def test_fees_reduce_net_pnl(self):
        trade = bt.Trade(entry_price=100.0, qty=1.0, entry_notional=100.0,
                          entry_is_maker=True, side="LONG")
        trade.close(exit_price=110.0, exit_reason="take_profit", exit_is_maker=True,
                    maker_fee_pct=1.0, taker_fee_pct=5.0)
        assert trade.fees > 0
        assert trade.net_pnl == trade.gross_pnl - trade.fees

    def test_short_loses_when_price_rises(self):
        trade = bt.Trade(entry_price=100.0, qty=1.0, entry_notional=100.0,
                          entry_is_maker=True, side="SHORT")
        trade.close(exit_price=110.0, exit_reason="stop_loss", exit_is_maker=False,
                    maker_fee_pct=0.0, taker_fee_pct=0.0)
        assert trade.gross_pnl == -10.0


class TestMaxDrawdown:
    def test_no_drawdown_on_monotonic_rise(self):
        assert bt.max_drawdown([100, 110, 120, 130]) == 0.0

    def test_drawdown_from_peak(self):
        # peak 120, trough 90 -> (120-90)/120 * 100 = 25%
        dd = bt.max_drawdown([100, 120, 90, 130])
        assert round(dd, 2) == 25.0

    def test_takes_worst_drawdown_across_multiple_peaks(self):
        dd = bt.max_drawdown([100, 200, 150, 300, 60])
        # worst is 300 -> 60 = 80%
        assert round(dd, 2) == 80.0


class _AlwaysLongOneCandleStrategy(Strategy):
    """Test double: enters LONG on the first candle it sees, exits on
    the very next candle - used to exercise run_backtest's full loop
    (entry -> exit -> equity tracking) without depending on any real
    strategy's signal logic."""
    name = "test_double"
    fee_gate_enabled = False

    def __init__(self, params):
        super().__init__(params)
        self._entered = False

    def min_candles_needed(self) -> int:
        return 1

    def compute_indicators(self, candles):
        return {"n": len(candles)}

    def entry_plan(self, price, candle, indicators) -> Optional[EntryPlan]:
        if self._entered:
            return None
        self._entered = True
        return EntryPlan(entry_price=price, stop_loss_price=price * 0.9,
                          take_profit_price=price * 1.1, is_maker=True, side="LONG")

    def exit_decision(self, position: Position, candle, indicators, bars_held):
        if bars_held >= 1:
            return "test_exit", candle["close"], True
        return None

    def status_line(self, price, indicators) -> str:
        return f"price={price}"


class TestRunBacktestIntegration:
    def test_opens_and_closes_one_position(self, base_config):
        candles = make_candles([100.0, 101.0, 102.0, 103.0])
        strategy = _AlwaysLongOneCandleStrategy({})
        trades, equity_curve = bt.run_backtest(strategy, candles, base_config)
        assert len(trades) == 1
        assert trades[0].side == "LONG"
        assert trades[0].exit_reason == "test_exit"

    def test_equity_curve_length_matches_candle_count_plus_one(self, base_config):
        candles = make_candles([100.0, 101.0, 102.0, 103.0])
        strategy = _AlwaysLongOneCandleStrategy({})
        trades, equity_curve = bt.run_backtest(strategy, candles, base_config)
        assert len(equity_curve) == len(candles) + 1

    def test_open_position_force_closed_at_end_of_data(self, base_config):
        # Only one candle, so exit_decision (which needs bars_held >= 1)
        # never fires - run_backtest must force-close at the end anyway.
        candles = make_candles([100.0])
        strategy = _AlwaysLongOneCandleStrategy({})
        trades, equity_curve = bt.run_backtest(strategy, candles, base_config)
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_data"
