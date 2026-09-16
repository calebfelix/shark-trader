"""SMA crossover (trend-following): fast/slow SMA cross, market orders
only, no fee gate. Kept for reference/comparison - not validated to the
same degree as mean_reversion. See CLAUDE.md before trading this live.
"""

from typing import Optional

from ..indicators import sma
from .base import EntryPlan, Position, Strategy


class SmaCrossoverStrategy(Strategy):
    name = "sma"
    fee_gate_enabled = False

    def __init__(self, params: dict):
        super().__init__(params)
        self._prev_fast_above_slow: Optional[bool] = None

    def min_candles_needed(self) -> int:
        return self.params["slow_period"] + 1

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        p = self.params
        closes = [c["close"] for c in candles]
        fast = sma(closes, p["fast_period"])
        slow = sma(closes, p["slow_period"])
        if fast is None or slow is None:
            return None
        return {"fast": fast, "slow": slow, "fast_above_slow": fast > slow}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None or self._prev_fast_above_slow is None:
            return None
        crossed_up = indicators["fast_above_slow"] and not self._prev_fast_above_slow
        if not crossed_up:
            return None

        p = self.params
        stop_loss_price = price * (1 - p["stop_loss_pct"] / 100.0)
        take_profit_price = price * (1 + p["take_profit_pct"] / 100.0)
        return EntryPlan(entry_price=price, stop_loss_price=stop_loss_price,
                          take_profit_price=take_profit_price, is_maker=False, side="LONG")

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        if indicators is None:
            return None
        if candle["low"] <= position.stop_loss_price:
            return "stop_loss", position.stop_loss_price, False
        if candle["high"] >= position.take_profit_price:
            return "take_profit", position.take_profit_price, False
        if self._prev_fast_above_slow is not None:
            crossed_down = (not indicators["fast_above_slow"]) and self._prev_fast_above_slow
            if crossed_down:
                return "signal_exit", candle["close"], False
        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up, not enough candle history yet"
        return (f"price={price:.2f} fast_sma={indicators['fast']:.2f} "
                f"slow_sma={indicators['slow']:.2f}")

    def note_indicators(self, indicators: Optional[dict]) -> None:
        if indicators is not None:
            self._prev_fast_above_slow = indicators["fast_above_slow"]
