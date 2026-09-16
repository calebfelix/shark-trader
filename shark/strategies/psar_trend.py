"""Parabolic SAR trend-following, long+short. Adapted for shark-trader
from a stop-and-reverse-style options positional system the user's firm
runs on Indian index options - here translated to a spot/futures
directional strategy since options-specific pieces (strike/moneyness
selection, expiry rollover) have no equivalent for a perpetual future.
See CLAUDE.md for the translation notes.

Unlike mean_reversion (fades extremes, stands aside when trending) and
scalp (fast mean-reversion, both sides), this is a pure trend-follower:
it flips into LONG when SAR crosses below price (uptrend) and SHORT when
SAR crosses above price (downtrend), then rides the trade with the SAR
level itself as a trailing stop - which is PSAR's whole design point -
rather than a fixed stop-loss percentage like the other strategies use.
A generous take_profit_pct exists only as a backstop cap, not the
primary exit; in a real trend the SAR trail is expected to be what
closes most winners.
"""

from typing import Optional

from .base import EntryPlan, Position, Strategy


class _ParabolicSAR:
    """Standard Wilder-style Parabolic SAR, updated one candle at a time.
    initial_af is both the starting acceleration factor and the amount it
    steps up by whenever a new extreme price is set; max_af caps it."""

    def __init__(self, initial_af: float, max_af: float):
        self.initial_af = initial_af
        self.max_af = max_af
        self.af = initial_af
        self.ep: Optional[float] = None
        self.sar: Optional[float] = None
        self.trend: Optional[str] = None

    def update(self, high: float, low: float, close: float) -> tuple[Optional[float], Optional[str]]:
        if self.sar is None:
            # First candle only: arbitrary but self-correcting starting
            # trend guess, same convention as the reference implementation.
            self.trend = "up" if close >= (high + low) / 2 else "down"
            self.sar = low if self.trend == "up" else high
            self.ep = high if self.trend == "up" else low
            return None, None

        self.sar += self.af * (self.ep - self.sar)

        if self.trend == "up" and low < self.sar:
            self.trend = "down"
            self.sar = self.ep
            self.ep = low
            self.af = self.initial_af
        elif self.trend == "down" and high > self.sar:
            self.trend = "up"
            self.sar = self.ep
            self.ep = high
            self.af = self.initial_af
        elif self.trend == "up" and high > self.ep:
            self.ep = high
            self.af = min(self.af + self.initial_af, self.max_af)
        elif self.trend == "down" and low < self.ep:
            self.ep = low
            self.af = min(self.af + self.initial_af, self.max_af)

        return self.sar, self.trend


class PSARTrendStrategy(Strategy):
    name = "psar_trend"
    # Trend-following, like sma.py - fee gate is for the fade/scalp
    # strategies where the edge per trade is small enough that fees can
    # eat it; a trend-follower's exits are sized by how far price runs,
    # not a fixed small target, so it isn't the same tradeoff.
    fee_gate_enabled = False

    def min_candles_needed(self) -> int:
        return 2

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        """Recomputes PSAR from scratch over the full candle history every
        call - same convention as mean_reversion.py/sma.py's BB/RSI/ATR
        (no cross-call instance state). An earlier version cached PSAR
        state incrementally keyed on how many candles it had already
        seen, which is only valid if `candles` is one list that only ever
        grows (true in backtest_engine.py's loop). It broke against
        live_engine.py, which calls exchange.get_klines() fresh every
        poll - a brand-new list each time, not a growing one - so the
        cached "already processed" count silently froze the PSAR state
        after the first poll instead of updating it. See CLAUDE.md."""
        calc = _ParabolicSAR(self.params["initial_af"], self.params["max_af"])
        trend_prev: Optional[str] = None
        sar_cur = trend_cur = None
        for c in candles:
            s, t = calc.update(c["high"], c["low"], c["close"])
            if s is not None:
                trend_prev = trend_cur if trend_cur is not None else trend_prev
                sar_cur, trend_cur = s, t

        if sar_cur is None:
            return None
        flipped = trend_prev is not None and trend_cur != trend_prev
        return {"sar": sar_cur, "trend": trend_cur, "flipped": flipped}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None or not indicators["flipped"]:
            return None

        p = self.params
        if indicators["trend"] == "up":
            entry_price = price * (1 + p["entry_limit_offset_pct"] / 100.0)
            stop_loss_price = entry_price * (1 - p["stop_loss_pct"] / 100.0)
            take_profit_price = entry_price * (1 + p["take_profit_pct"] / 100.0)
            side = "LONG"
        else:
            entry_price = price * (1 - p["entry_limit_offset_pct"] / 100.0)
            stop_loss_price = entry_price * (1 + p["stop_loss_pct"] / 100.0)
            take_profit_price = entry_price * (1 - p["take_profit_pct"] / 100.0)
            side = "SHORT"

        # stop_loss_price is a FIXED % distance, not the SAR value - this
        # is what position sizing uses as its risk-per-trade denominator
        # (see calc_position_size), so it must stay stable at entry time.
        # Using the entry-time SAR value here was a real bug: SAR can sit
        # very close to price right after a flip, which produced huge
        # position sizes decoupled from the actual (much wider, moving)
        # trailing-stop exit - inflating backtest returns into fantasy
        # territory. The SAR trail is still the primary exit (see
        # exit_decision), this fixed % is only a sizing basis + hard cap.
        return EntryPlan(entry_price=entry_price, stop_loss_price=stop_loss_price,
                          take_profit_price=take_profit_price, is_maker=True, side=side)

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        if indicators is None:
            return None
        sar = indicators["sar"]

        if position.side == "LONG":
            if candle["low"] <= position.stop_loss_price:
                return "stop_loss", position.stop_loss_price, False
            if candle["low"] <= sar:
                return "psar_trailing_stop", sar, False
            if candle["high"] >= position.take_profit_price:
                return "take_profit", position.take_profit_price, True
        else:
            if candle["high"] >= position.stop_loss_price:
                return "stop_loss", position.stop_loss_price, False
            if candle["high"] >= sar:
                return "psar_trailing_stop", sar, False
            if candle["low"] <= position.take_profit_price:
                return "take_profit", position.take_profit_price, True
        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up, not enough candle history yet"
        return f"price={price:.2f} SAR={indicators['sar']:.2f} trend={indicators['trend']}"
