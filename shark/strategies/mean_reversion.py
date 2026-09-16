"""Mean reversion: Bollinger Bands + RSI entries, ATR trend filter, maker
orders for entry/take-profit, market order for stop-loss. See CLAUDE.md
"Design decisions worth preserving" for why this is the validated
default strategy.
"""

from typing import Optional

from ..indicators import atr, bollinger_bands, rsi
from .base import EntryPlan, Position, Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    fee_gate_enabled = True

    def min_candles_needed(self) -> int:
        p = self.params
        return max(p["bb_period"], p["rsi_period"], p["atr_period"]) + 1

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        p = self.params
        closes = [c["close"] for c in candles]
        upper, mid, lower = bollinger_bands(closes, p["bb_period"], p["bb_stddev"])
        rsi_val = rsi(closes, p["rsi_period"])
        atr_val = atr(candles, p["atr_period"])
        if upper is None or rsi_val is None or atr_val is None:
            return None

        price = closes[-1]
        atr_pct = (atr_val / price) * 100
        is_trending = atr_pct > p["atr_trend_threshold_pct"]
        return {
            "upper": upper, "mid": mid, "lower": lower,
            "rsi": rsi_val, "atr_pct": atr_pct, "is_trending": is_trending,
        }

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None or indicators["is_trending"]:
            return None
        if price > indicators["lower"] or indicators["rsi"] > self.params["rsi_oversold"]:
            return None

        p = self.params
        entry_price = price * (1 + p["entry_limit_offset_pct"] / 100.0)
        stop_loss_price = entry_price * (1 - p["stop_loss_pct"] / 100.0)
        take_profit_price = entry_price * (1 + p["take_profit_pct"] / 100.0)
        return EntryPlan(entry_price=entry_price, stop_loss_price=stop_loss_price,
                          take_profit_price=take_profit_price, is_maker=True, side="LONG")

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        if indicators is None:
            return None
        if candle["low"] <= position.stop_loss_price:
            return "stop_loss", position.stop_loss_price, False
        if candle["high"] >= position.take_profit_price:
            return "take_profit", position.take_profit_price, True
        if indicators["rsi"] >= self.params["rsi_overbought"]:
            return "rsi_overbought", candle["close"], True
        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up, not enough candle history yet"
        trend = "(TRENDING - standing aside)" if indicators["is_trending"] else "(range-bound)"
        return (f"price={price:.2f} BB[{indicators['lower']:.2f},{indicators['mid']:.2f},"
                f"{indicators['upper']:.2f}] RSI={indicators['rsi']:.1f} "
                f"ATR%={indicators['atr_pct']:.2f} {trend}")
