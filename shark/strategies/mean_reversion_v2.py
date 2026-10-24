"""Mean reversion v2: long and short fades of Bollinger-band extremes, taken only when the market is
ranging (Kaufman efficiency ratio low), confirmed by a reversal candle, with an ATR-sized stop, the
middle band as the target and a time stop. Kept separate from `mean_reversion` (the original) so the
two can be compared; nothing here is wired into a running bot.
"""

from typing import Optional

from ..indicators import atr, bollinger_bands, rsi
from .base import EntryPlan, Position, Strategy


def efficiency_ratio(closes: list[float], period: int) -> Optional[float]:
    """|net move| / total path over `period` bars: ~1 in a clean trend, ~0 in chop."""
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    return abs(window[-1] - window[0]) / path if path else 0.0


class MeanReversionV2Strategy(Strategy):
    name = "mean_reversion_v2"
    fee_gate_enabled = True

    def min_candles_needed(self) -> int:
        p = self.params
        return max(p["bb_period"], p["rsi_period"], p["atr_period"], p["er_period"]) + 2

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        p = self.params
        window = candles[-(self.min_candles_needed() + 5):]   # indicators only need a short tail
        closes = [c["close"] for c in window]
        upper, mid, lower = bollinger_bands(closes, p["bb_period"], p["bb_stddev"])
        rsi_val = rsi(closes, p["rsi_period"])
        atr_val = atr(window, p["atr_period"])
        er = efficiency_ratio(closes, p["er_period"])
        if upper is None or rsi_val is None or atr_val is None or er is None:
            return None
        return {"upper": upper, "mid": mid, "lower": lower, "rsi": rsi_val, "atr": atr_val,
                "er": er, "ranging": er <= p["er_max"]}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None or not indicators["ranging"]:
            return None
        p = self.params
        side = None
        if price < indicators["lower"] and indicators["rsi"] <= p["rsi_oversold"]:
            if not p["require_reversal"] or candle["close"] > candle["open"]:
                side = "LONG"
        elif p["allow_short"] and price > indicators["upper"] and indicators["rsi"] >= p["rsi_overbought"]:
            if not p["require_reversal"] or candle["close"] < candle["open"]:
                side = "SHORT"
        if side is None:
            return None
        stop_dist = indicators["atr"] * p["stop_atr_mult"]
        sign = 1 if side == "LONG" else -1
        return EntryPlan(entry_price=price, stop_loss_price=price - sign * stop_dist,
                         take_profit_price=indicators["mid"], is_maker=False, side=side)

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                      bars_held: int) -> Optional[tuple[str, float, bool]]:
        long = position.side == "LONG"
        if (candle["low"] <= position.stop_loss_price) if long else (candle["high"] >= position.stop_loss_price):
            return "stop_loss", position.stop_loss_price, False
        if indicators is not None:
            mid = indicators["mid"]
            if (candle["high"] >= mid) if long else (candle["low"] <= mid):
                return "take_profit", mid, True
        if bars_held >= self.params["max_bars"]:
            return "time_stop", candle["close"], False
        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up"
        regime = "ranging" if indicators["ranging"] else "TRENDING - standing aside"
        return (f"price={price:.2f} BB[{indicators['lower']:.2f},{indicators['mid']:.2f},"
                f"{indicators['upper']:.2f}] RSI={indicators['rsi']:.1f} ER={indicators['er']:.2f} ({regime})")
