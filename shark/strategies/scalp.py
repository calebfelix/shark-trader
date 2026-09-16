"""Scalp: fast/tight Bollinger Bands + RSI mean-reversion on 1m candles,
long AND short. Exits toward the middle band, on a fee-covering
take-profit, on stop-loss, or after max_hold_candles bars with no other
exit fired (keeps capital turning over - the whole point of scalping).

Long-side signal shape mirrors mean_reversion.py, just faster/tighter.
Short side is the mirror image: sell near the upper band when RSI is
overbought instead of buying near the lower band when oversold.

Short-selling against the live order endpoint has not been tested with a
real order (nothing in this project has ever placed one - see CLAUDE.md).
Validate via backtest and dry-run before ever flipping dry_run to false.
"""

from typing import Optional

from ..indicators import bollinger_bands, rsi
from .base import EntryPlan, Position, Strategy


class ScalpStrategy(Strategy):
    name = "scalp"
    fee_gate_enabled = True

    def min_candles_needed(self) -> int:
        p = self.params
        return max(p["bb_period"], p["rsi_period"]) + 1

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        p = self.params
        closes = [c["close"] for c in candles]
        upper, mid, lower = bollinger_bands(closes, p["bb_period"], p["bb_stddev"])
        rsi_val = rsi(closes, p["rsi_period"])
        if upper is None or rsi_val is None:
            return None
        return {"upper": upper, "mid": mid, "lower": lower, "rsi": rsi_val}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None:
            return None
        p = self.params

        if price <= indicators["lower"] and indicators["rsi"] <= p["rsi_oversold"]:
            entry_price = price * (1 + p["entry_limit_offset_pct"] / 100.0)
            stop_loss_price = entry_price * (1 - p["stop_loss_pct"] / 100.0)
            take_profit_price = entry_price * (1 + p["take_profit_pct"] / 100.0)
            return EntryPlan(entry_price=entry_price, stop_loss_price=stop_loss_price,
                              take_profit_price=take_profit_price, is_maker=True, side="LONG")

        if price >= indicators["upper"] and indicators["rsi"] >= p["rsi_overbought"]:
            entry_price = price * (1 - p["entry_limit_offset_pct"] / 100.0)
            stop_loss_price = entry_price * (1 + p["stop_loss_pct"] / 100.0)
            take_profit_price = entry_price * (1 - p["take_profit_pct"] / 100.0)
            return EntryPlan(entry_price=entry_price, stop_loss_price=stop_loss_price,
                              take_profit_price=take_profit_price, is_maker=True, side="SHORT")

        return None

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        # Checked first, even with missing indicators, so a data gap can't
        # keep a scalp position open past its intended max hold time.
        if bars_held >= self.params["max_hold_candles"]:
            return "max_hold_time", candle["close"], False

        if indicators is None:
            return None

        if position.side == "LONG":
            if candle["low"] <= position.stop_loss_price:
                return "stop_loss", position.stop_loss_price, False
            if candle["high"] >= position.take_profit_price:
                return "take_profit", position.take_profit_price, True
            if candle["close"] >= indicators["mid"]:
                return "mid_band", candle["close"], True
        else:  # SHORT
            if candle["high"] >= position.stop_loss_price:
                return "stop_loss", position.stop_loss_price, False
            if candle["low"] <= position.take_profit_price:
                return "take_profit", position.take_profit_price, True
            if candle["close"] <= indicators["mid"]:
                return "mid_band", candle["close"], True

        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up, not enough candle history yet"
        return (f"price={price:.2f} BB[{indicators['lower']:.2f},{indicators['mid']:.2f},"
                f"{indicators['upper']:.2f}] RSI={indicators['rsi']:.1f}")
