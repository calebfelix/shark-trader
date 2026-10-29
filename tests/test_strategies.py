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


class TestMeanReversionV2:
    P = {"bb_period": 20, "bb_stddev": 2.0, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
         "atr_period": 14, "er_period": 20, "er_max": 0.3, "stop_atr_mult": 2.0, "max_bars": 5,
         "require_reversal": False, "allow_short": True, "min_profit_to_fee_ratio": 1.5}

    def _s(self, **kw):
        from shark.strategies.mean_reversion_v2 import MeanReversionV2Strategy
        return MeanReversionV2Strategy({**self.P, **kw})

    def _ind(self, **kw):
        return {"upper": 105.0, "mid": 100.0, "lower": 95.0, "rsi": 20.0, "atr": 1.0, "er": 0.1,
                "ranging": True, **kw}

    def test_long_below_lower_band(self):
        plan = self._s().entry_plan(94.0, {"open": 95, "close": 94}, self._ind())
        assert plan.side == "LONG" and plan.take_profit_price == 100.0 and plan.stop_loss_price == 92.0

    def test_short_above_upper_band(self):
        plan = self._s().entry_plan(106.0, {"open": 105, "close": 106}, self._ind(rsi=80.0))
        assert plan.side == "SHORT" and plan.stop_loss_price == 108.0

    def test_stands_aside_when_trending(self):
        assert self._s().entry_plan(94.0, {"open": 95, "close": 94}, self._ind(ranging=False)) is None

    def test_reversal_candle_required(self):
        s = self._s(require_reversal=True)
        assert s.entry_plan(94.0, {"open": 95, "close": 94}, self._ind()) is None
        assert s.entry_plan(94.0, {"open": 93, "close": 94}, self._ind()) is not None

    def test_exits(self):
        from shark.strategies.base import Position
        s = self._s()
        long = Position(94, 1, 94, 92, 100, False, "LONG")
        assert s.exit_decision(long, {"low": 91, "high": 95}, self._ind(), 1)[0] == "stop_loss"
        assert s.exit_decision(long, {"low": 94, "high": 101}, self._ind(), 1) == ("take_profit", 100.0, True)
        assert s.exit_decision(long, {"low": 94, "high": 96, "close": 95}, self._ind(), 5)[0] == "time_stop"
        assert s.exit_decision(long, {"low": 94, "high": 96, "close": 95}, self._ind(), 2) is None
        short = Position(106, 1, 106, 108, 100, False, "SHORT")
        assert s.exit_decision(short, {"low": 99, "high": 107}, self._ind(), 1) == ("take_profit", 100.0, True)

    def test_efficiency_ratio(self):
        from shark.strategies.mean_reversion_v2 import efficiency_ratio
        assert efficiency_ratio([1, 2, 3, 4, 5], 4) == 1.0
        assert efficiency_ratio([1, 2, 1, 2, 1], 4) == 0.0


class TestSupplyDemand:
    P = {"atr_period": 14, "imp_mult": 1.8, "base_body_frac": 0.6, "max_base": 3, "max_age": 25, "max_zone_atr": 1.5,
         "wick_frac": 0.4, "stop_buffer_atr": 0.25, "min_stop_atr": 0.4, "max_stop_atr": 3.0, "rr": 2.0,
         "max_bars": 10, "trend_filter": False, "trend_period": 200, "min_profit_to_fee_ratio": 2.0,
         "fallback_stop_pct": 1.5}

    @staticmethod
    def c(o, h, l, cl):
        return {"open": o, "high": h, "low": l, "close": cl, "volume": 1.0}

    def _s(self, **kw):
        from shark.strategies.supply_demand import SupplyDemandStrategy
        return SupplyDemandStrategy({**self.P, **kw})

    def _demand_series(self, tail):
        """Flat chop around 100 (range ~2), a tiny base at 99.5-100, a +6 impulse, a drift up, then `tail`."""
        c = self.c
        out = [c(100, 101, 99, 100.2), c(100.2, 101, 99, 99.8)] * 20
        out += [c(99.8, 100.1, 99.5, 100.0)]                          # base: body 0.2, low 99.5
        out += [c(100.0, 106.2, 99.9, 106.0)]                         # impulse: body 6 >> 1.8 x ATR(~2), breaks the base
        out += [c(106.0, 108, 105.5, 107.5), c(107.5, 109, 107, 108.5), c(108.5, 109.5, 108, 109)]
        return out + tail

    def test_rejection_of_fresh_demand_zone_signals_long(self):
        ind = self._s().compute_indicators(self._demand_series([self.c(108, 108.2, 99.6, 105.0)]))
        # a deep retrace tags the zone (low 99.6 <= proximal 100.0) and closes far above it, long lower wick
        sig = ind["signal"]
        assert sig and sig["side"] == "LONG" and sig["stop"] < 99.5
        plan = self._s().entry_plan(105.0, {}, ind)
        assert plan.side == "LONG" and plan.stop_loss_price == sig["stop"]
        assert abs((plan.take_profit_price - 105.0) - 2.0 * (105.0 - sig["stop"])) < 1e-9

    def test_no_signal_without_a_touch(self):
        ind = self._s().compute_indicators(self._demand_series([self.c(109, 109.5, 108, 108.5)]))
        assert ind["signal"] is None and ind["zones"] >= 1

    def test_used_zone_is_stale(self):
        touched = self._demand_series([self.c(109, 109.2, 99.8, 105.0)])            # first touch, already happened
        ind = self._s().compute_indicators(touched + [self.c(105, 106, 99.7, 104.0)])   # a second visit
        assert ind["signal"] is None

    def test_close_through_the_zone_is_not_a_rejection(self):
        ind = self._s().compute_indicators(self._demand_series([self.c(109, 109.2, 98.0, 99.0)]))
        assert ind["signal"] is None

    def test_supply_zone_mirror(self):
        c = self.c
        out = [c(100, 101, 99, 99.8), c(99.8, 101, 99, 100.2)] * 20
        out += [c(100.2, 100.5, 99.9, 100.0)]
        out += [c(100.0, 100.1, 93.8, 94.0)]
        out += [c(94, 94.5, 92, 92.5), c(92.5, 93, 91, 91.5), c(91.5, 92, 90.5, 91)]
        out += [c(92, 100.3, 91.8, 95.0)]                                           # retrace into the zone, rejected
        sig = self._s().compute_indicators(out)["signal"]
        assert sig and sig["side"] == "SHORT" and sig["stop"] > 100.5

    def test_exits(self):
        from shark.strategies.base import Position
        s = self._s()
        long = Position(105, 1, 105, 99, 117, False, "LONG")
        assert s.exit_decision(long, {"low": 98, "high": 106}, None, 1)[0] == "stop_loss"
        assert s.exit_decision(long, {"low": 104, "high": 118}, None, 1) == ("take_profit", 117, True)
        assert s.exit_decision(long, {"low": 104, "high": 106, "close": 105}, None, 10)[0] == "time_stop"
        assert s.exit_decision(long, {"low": 104, "high": 106, "close": 105}, None, 3) is None
