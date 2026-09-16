from shark.strategies.base import Position
from shark.strategies.mean_reversion import MeanReversionStrategy
from shark.strategies.psar_trend import PSARTrendStrategy, _ParabolicSAR
from shark.strategies.scalp import ScalpStrategy
from shark.strategies.sma import SmaCrossoverStrategy

from .conftest import make_candles

MR_PARAMS = dict(
    bb_period=5, bb_stddev=2.0, rsi_period=5, rsi_oversold=30, rsi_overbought=70,
    atr_period=5, atr_trend_threshold_pct=0.5, stop_loss_pct=1.5, take_profit_pct=0.8,
    entry_limit_offset_pct=0.02, min_profit_to_fee_ratio=3.0,
)

PSAR_PARAMS = dict(initial_af=0.02, max_af=0.2, entry_limit_offset_pct=0.01,
                    stop_loss_pct=2.0, take_profit_pct=6.0)

SCALP_PARAMS = dict(bb_period=5, bb_stddev=2.0, rsi_period=5, rsi_oversold=25,
                     rsi_overbought=75, stop_loss_pct=0.3, take_profit_pct=0.4,
                     entry_limit_offset_pct=0.02, min_profit_to_fee_ratio=2.0,
                     max_hold_candles=3)

SMA_PARAMS = dict(fast_period=2, slow_period=4, stop_loss_pct=1.5, take_profit_pct=3.0)


class TestMeanReversionStrategy:
    def _strategy(self):
        return MeanReversionStrategy(dict(MR_PARAMS))

    def test_compute_indicators_none_when_insufficient_history(self):
        strategy = self._strategy()
        candles = make_candles([100.0, 101.0])
        assert strategy.compute_indicators(candles) is None

    def test_no_entry_when_trending(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 20, "is_trending": True}
        plan = strategy.entry_plan(price=94.0, candle={}, indicators=indicators)
        assert plan is None

    def test_no_entry_when_price_above_lower_band(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 20, "is_trending": False}
        plan = strategy.entry_plan(price=96.0, candle={}, indicators=indicators)
        assert plan is None

    def test_no_entry_when_rsi_not_oversold(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 50, "is_trending": False}
        plan = strategy.entry_plan(price=94.0, candle={}, indicators=indicators)
        assert plan is None

    def test_entry_when_ranging_and_oversold(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 20, "is_trending": False}
        plan = strategy.entry_plan(price=94.0, candle={}, indicators=indicators)
        assert plan is not None
        assert plan.side == "LONG"
        assert plan.stop_loss_price < plan.entry_price < plan.take_profit_price

    def test_exit_priority_stop_loss_before_take_profit(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=98.0, take_profit_price=102.0,
                             entry_is_maker=True, side="LONG")
        # A candle whose range crosses BOTH the stop and the TP - stop must win.
        candle = {"low": 97.0, "high": 103.0, "close": 100.0}
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 50, "is_trending": False}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=1)
        assert decision[0] == "stop_loss"

    def test_exit_on_rsi_overbought(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=98.0, take_profit_price=110.0,
                             entry_is_maker=True, side="LONG")
        candle = {"low": 99.0, "high": 101.0, "close": 101.0}
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 80, "is_trending": False}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=1)
        assert decision[0] == "rsi_overbought"

    def test_no_exit_when_indicators_missing(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=98.0, take_profit_price=110.0,
                             entry_is_maker=True, side="LONG")
        candle = {"low": 90.0, "high": 120.0, "close": 100.0}
        assert strategy.exit_decision(position, candle, None, bars_held=1) is None


class TestParabolicSARCalculator:
    def test_first_update_returns_none(self):
        calc = _ParabolicSAR(initial_af=0.02, max_af=0.2)
        sar, trend = calc.update(high=11.0, low=9.0, close=10.5)
        assert sar is None and trend is None

    def test_sustained_uptrend_keeps_trend_up(self):
        calc = _ParabolicSAR(initial_af=0.02, max_af=0.2)
        calc.update(high=10.0, low=9.0, close=9.5)
        last_trend = None
        for price in [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]:
            sar, trend = calc.update(high=price + 0.5, low=price - 0.5, close=price)
            last_trend = trend
        assert last_trend == "up"

    def test_sharp_reversal_flips_trend(self):
        calc = _ParabolicSAR(initial_af=0.02, max_af=0.2)
        calc.update(high=10.0, low=9.0, close=9.5)
        for price in [10.0, 11.0, 12.0, 13.0]:
            calc.update(high=price + 0.5, low=price - 0.5, close=price)
        # Sharp drop well below the accumulated SAR should flip it to "down".
        sar, trend = calc.update(high=5.0, low=1.0, close=2.0)
        assert trend == "down"


class TestPSARTrendStrategy:
    def _strategy(self):
        return PSARTrendStrategy(dict(PSAR_PARAMS))

    def test_no_entry_without_flip(self):
        strategy = self._strategy()
        indicators = {"sar": 95.0, "trend": "up", "flipped": False}
        assert strategy.entry_plan(price=100.0, candle={}, indicators=indicators) is None

    def test_long_entry_on_up_flip(self):
        strategy = self._strategy()
        indicators = {"sar": 95.0, "trend": "up", "flipped": True}
        plan = strategy.entry_plan(price=100.0, candle={}, indicators=indicators)
        assert plan.side == "LONG"

    def test_short_entry_on_down_flip(self):
        strategy = self._strategy()
        indicators = {"sar": 105.0, "trend": "down", "flipped": True}
        plan = strategy.entry_plan(price=100.0, candle={}, indicators=indicators)
        assert plan.side == "SHORT"

    def test_stop_loss_is_fixed_pct_not_sar_value(self):
        # Regression for the real bug in CLAUDE.md: stop_loss_price must be
        # a fixed % distance from entry, NOT the entry-time SAR value (SAR
        # can sit very close to price right after a flip, which produced
        # fantasy-sized positions when used as the sizing denominator).
        strategy = self._strategy()
        sar_very_close_to_price = 99.5  # only 0.5% away from price=100
        indicators = {"sar": sar_very_close_to_price, "trend": "up", "flipped": True}
        plan = strategy.entry_plan(price=100.0, candle={}, indicators=indicators)
        stop_distance_pct = abs(plan.entry_price - plan.stop_loss_price) / plan.entry_price * 100
        assert plan.stop_loss_price != sar_very_close_to_price
        assert round(stop_distance_pct, 2) == PSAR_PARAMS["stop_loss_pct"]

    def test_exit_priority_stop_loss_before_sar_trail(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=98.0, take_profit_price=106.0,
                             entry_is_maker=True, side="LONG")
        # Candle low crosses both the fixed stop AND a SAR that's even higher.
        candle = {"low": 97.0, "high": 100.0, "close": 99.0}
        indicators = {"sar": 99.0, "trend": "up", "flipped": False}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=5)
        assert decision[0] == "stop_loss"

    def test_sar_trail_exit_for_short(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=102.0, take_profit_price=94.0,
                             entry_is_maker=True, side="SHORT")
        candle = {"low": 99.0, "high": 100.5, "close": 100.0}
        indicators = {"sar": 100.5, "trend": "down", "flipped": False}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=5)
        assert decision[0] == "psar_trailing_stop"


class TestSmaCrossoverStrategy:
    def _strategy(self):
        return SmaCrossoverStrategy(dict(SMA_PARAMS))

    def test_no_entry_before_any_prior_state(self):
        strategy = self._strategy()
        indicators = {"fast": 10.0, "slow": 9.0, "fast_above_slow": True}
        assert strategy.entry_plan(price=10.0, candle={}, indicators=indicators) is None

    def test_entry_on_cross_up(self):
        strategy = self._strategy()
        strategy.note_indicators({"fast": 9.0, "slow": 10.0, "fast_above_slow": False})
        indicators = {"fast": 11.0, "slow": 10.0, "fast_above_slow": True}
        plan = strategy.entry_plan(price=11.0, candle={}, indicators=indicators)
        assert plan is not None
        assert plan.side == "LONG"

    def test_no_entry_when_already_above_no_new_cross(self):
        strategy = self._strategy()
        strategy.note_indicators({"fast": 11.0, "slow": 10.0, "fast_above_slow": True})
        indicators = {"fast": 12.0, "slow": 10.0, "fast_above_slow": True}
        assert strategy.entry_plan(price=12.0, candle={}, indicators=indicators) is None

    def test_exit_on_cross_down(self):
        strategy = self._strategy()
        strategy.note_indicators({"fast": 11.0, "slow": 10.0, "fast_above_slow": True})
        position = Position(entry_price=11.0, qty=1.0, entry_notional=11.0,
                             stop_loss_price=9.0, take_profit_price=20.0,
                             entry_is_maker=False, side="LONG")
        candle = {"low": 10.5, "high": 10.5, "close": 10.5}
        indicators = {"fast": 9.5, "slow": 10.0, "fast_above_slow": False}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=1)
        assert decision[0] == "signal_exit"


class TestScalpStrategy:
    def _strategy(self):
        return ScalpStrategy(dict(SCALP_PARAMS))

    def test_long_entry_signal(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 20}
        plan = strategy.entry_plan(price=94.0, candle={}, indicators=indicators)
        assert plan.side == "LONG"

    def test_short_entry_signal(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 80}
        plan = strategy.entry_plan(price=111.0, candle={}, indicators=indicators)
        assert plan.side == "SHORT"

    def test_no_entry_mid_range(self):
        strategy = self._strategy()
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 50}
        assert strategy.entry_plan(price=100.0, candle={}, indicators=indicators) is None

    def test_max_hold_exit_fires_even_without_indicators(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=99.0, take_profit_price=101.0,
                             entry_is_maker=True, side="LONG")
        candle = {"low": 99.5, "high": 100.5, "close": 100.0}
        decision = strategy.exit_decision(position, candle, None, bars_held=3)
        assert decision[0] == "max_hold_time"

    def test_no_exit_before_max_hold_without_indicators(self):
        strategy = self._strategy()
        position = Position(entry_price=100.0, qty=1.0, entry_notional=100.0,
                             stop_loss_price=99.0, take_profit_price=101.0,
                             entry_is_maker=True, side="LONG")
        candle = {"low": 99.5, "high": 100.5, "close": 100.0}
        assert strategy.exit_decision(position, candle, None, bars_held=1) is None

    def test_mid_band_exit_for_short(self):
        strategy = self._strategy()
        # stop_loss/take_profit set far away so neither triggers on this
        # candle - only the mid-band signal exit should fire.
        position = Position(entry_price=110.0, qty=1.0, entry_notional=110.0,
                             stop_loss_price=115.0, take_profit_price=90.0,
                             entry_is_maker=True, side="SHORT")
        candle = {"low": 95.0, "high": 101.0, "close": 99.5}
        indicators = {"upper": 110, "mid": 100, "lower": 95, "rsi": 40}
        decision = strategy.exit_decision(position, candle, indicators, bars_held=1)
        assert decision[0] == "mid_band"
