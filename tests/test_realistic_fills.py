"""The backtest must not give credit for prices a real order can't get."""

import pytest

from shark.backtest_engine import realistic_exit, run_backtest
from shark.strategies.base import EntryPlan, Position, Strategy
from shark.strategies.psar_trend import PSARTrendStrategy

SLIP = 0.0002  # 0.02%


class TestRealisticExit:
    def test_long_stop_that_gaps_fills_at_the_worse_open_plus_slippage(self):
        px, maker = realistic_exit("LONG", "stop_loss", 100.0, False, candle_open=97.0, slippage=SLIP)
        assert px == pytest.approx(97.0 * (1 - SLIP)) and maker is False

    def test_long_stop_without_gap_fills_at_its_level_plus_slippage(self):
        px, _ = realistic_exit("LONG", "stop_loss", 100.0, False, candle_open=103.0, slippage=SLIP)
        assert px == pytest.approx(100.0 * (1 - SLIP))

    def test_short_stop_gap_up_is_worse_for_the_short(self):
        px, _ = realistic_exit("SHORT", "psar_trailing_stop", 100.0, False, candle_open=104.0, slippage=SLIP)
        assert px == pytest.approx(104.0 * (1 + SLIP))

    def test_signal_exit_called_maker_is_still_a_taker_market_order(self):
        px, maker = realistic_exit("LONG", "rsi_overbought", 105.0, True, candle_open=104.0, slippage=SLIP)
        assert maker is False and px == pytest.approx(105.0 * (1 - SLIP))

    def test_resting_take_profit_keeps_its_level_and_maker_fee(self):
        px, maker = realistic_exit("LONG", "take_profit", 110.0, True, candle_open=104.0, slippage=SLIP)
        assert px == 110.0 and maker is True


class OneTrade(Strategy):
    name = "one"
    fee_gate_enabled = False

    def __init__(self):
        super().__init__({"kline_interval": "1h"})
        self.opened = False

    def min_candles_needed(self): return 1
    def compute_indicators(self, candles): return {}

    def entry_plan(self, price, candle, indicators):
        if self.opened:
            return None
        self.opened = True
        return EntryPlan(price * 1.0001, price * 0.98, price * 1.06, True, "LONG")   # a "maker" limit through the market

    def exit_decision(self, position, candle, indicators, bars_held):
        return ("rsi_overbought", candle["close"], True) if bars_held >= 1 else None   # "maker" at the close


def _candles():
    return [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0},
            {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0, "volume": 1.0},
            {"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 1.0}]


CFG = {"fees": {"maker_fee_pct": 0.016, "taker_fee_pct": 0.04, "gst_pct": 18}, "account_capital": 5000.0,
       "risk_per_trade_pct": 1.0, "backtest": {}}


def test_realistic_mode_charges_taker_fees_and_slippage_on_both_legs():
    (ideal,), _ = run_backtest(OneTrade(), _candles(), dict(CFG, backtest={"realistic_fills": False}))
    (real,), _ = run_backtest(OneTrade(), _candles(), dict(CFG, backtest={"realistic_fills": True, "slippage_pct": 0.02}))
    assert ideal.entry_is_maker is True and real.entry_is_maker is False
    assert real.fees > ideal.fees * 2                    # taker both ways vs maker both ways
    assert real.net_pnl < ideal.net_pnl                  # strictly worse than the idealised fill


def test_realistic_is_the_default():
    (t,), _ = run_backtest(OneTrade(), _candles(), dict(CFG, backtest={}))
    assert t.entry_is_maker is False


PSAR = {"kline_interval": "1h", "initial_af": 0.03, "max_af": 0.3, "entry_limit_offset_pct": 0.01,
        "stop_loss_pct": 2.0, "take_profit_pct": 6.0}


def _uptrend_then_sharp_drop():
    cs = [{"open": 100 + i, "high": 101 + i, "low": 99.5 + i, "close": 100.6 + i, "volume": 1.0} for i in range(30)]
    last = cs[-1]["close"]
    cs.append({"open": last, "high": last + 0.2, "low": last - 6.0, "close": last - 5.0, "volume": 1.0})
    return cs


def test_psar_trailing_exit_price_is_the_stop_level_not_the_unreachable_trend_extreme():
    strat = PSARTrendStrategy(PSAR)
    candles = _uptrend_then_sharp_drop()
    ind = strat.compute_indicators(candles)
    best_high = max(c["high"] for c in candles[:-1])
    assert ind["flipped"] and ind["trend"] == "down"
    assert ind["sar"] == pytest.approx(best_high)             # post-flip SAR = the trend's extreme (unreachable)
    assert ind["stop_level"] < best_high - 1                  # the level the stop really sat at is far lower
    pos = Position(entry_price=120.0, qty=1.0, entry_notional=120.0, stop_loss_price=1.0, take_profit_price=999.0,
                   entry_is_maker=False, side="LONG")
    reason, price, maker = strat.exit_decision(pos, candles[-1], ind, 1)
    assert reason == "psar_trailing_stop" and price == pytest.approx(ind["stop_level"]) and maker is False
