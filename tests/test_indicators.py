from shark.indicators import atr, bollinger_bands, rsi, sma

from .conftest import make_candles


class TestBollingerBands:
    def test_none_when_insufficient_data(self):
        assert bollinger_bands([1.0, 2.0], period=5, stddev_mult=2.0) == (None, None, None)

    def test_flat_series_has_zero_width_bands(self):
        upper, mid, lower = bollinger_bands([10.0] * 5, period=5, stddev_mult=2.0)
        assert upper == mid == lower == 10.0

    def test_upper_and_lower_straddle_mid(self):
        closes = [10.0, 11.0, 9.0, 12.0, 8.0]
        upper, mid, lower = bollinger_bands(closes, period=5, stddev_mult=2.0)
        assert lower < mid < upper
        assert upper - mid == mid - lower  # symmetric around mid by construction

    def test_only_uses_trailing_window(self):
        # An extra, very different candle further back should not affect
        # a shorter trailing window's bands.
        closes = [1000.0, 10.0, 11.0, 9.0, 12.0, 8.0]
        _, mid_with_history, _ = bollinger_bands(closes, period=5, stddev_mult=2.0)
        _, mid_no_history, _ = bollinger_bands(closes[1:], period=5, stddev_mult=2.0)
        assert mid_with_history == mid_no_history


class TestRSI:
    def test_none_when_insufficient_data(self):
        assert rsi([1.0, 2.0, 3.0], period=14) is None

    def test_all_gains_is_100(self):
        closes = [float(i) for i in range(1, 16)]  # strictly increasing
        assert rsi(closes, period=14) == 100.0

    def test_all_losses_is_0(self):
        closes = [float(i) for i in range(15, 0, -1)]  # strictly decreasing
        assert rsi(closes, period=14) == 0.0

    def test_mixed_moves_is_between_bounds(self):
        closes = [10, 11, 9, 12, 8, 13, 7, 14, 6, 15, 5, 16, 4, 17, 3]
        closes = [float(c) for c in closes]
        value = rsi(closes, period=14)
        assert 0.0 < value < 100.0


class TestATR:
    def test_none_when_insufficient_data(self):
        candles = make_candles([10.0, 11.0])
        assert atr(candles, period=14) is None

    def test_positive_for_moving_price(self):
        candles = make_candles([10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 7.0, 14.0, 6.0, 15.0,
                                 5.0, 16.0, 4.0, 17.0, 3.0])
        value = atr(candles, period=14)
        assert value > 0

    def test_zero_for_perfectly_flat_candles(self):
        # high == low == close every candle, and close never moves ->
        # every component of true range is 0.
        candles = [{"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0}
                   for _ in range(20)]
        assert atr(candles, period=14) == 0.0


class TestSMA:
    def test_none_when_insufficient_data(self):
        assert sma([1.0, 2.0], period=5) is None

    def test_simple_average(self):
        assert sma([1.0, 2.0, 3.0, 4.0, 5.0], period=5) == 3.0

    def test_only_uses_trailing_window(self):
        assert sma([1000.0, 1.0, 2.0, 3.0], period=3) == 2.0
