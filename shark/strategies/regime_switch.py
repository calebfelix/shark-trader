"""Regime-switching hybrid: routes between mean_reversion (ranging market)
and psar_trend (trending market) using mean_reversion's own ATR trend
filter as the regime signal - no new indicator needed, since that filter
already exists to answer exactly this question ("is the market trending
right now?").

This wraps the two existing, independently-validated strategies rather
than reimplementing their signal logic - regime_switch owns only the
routing decision. See CLAUDE.md for why: mean_reversion stands aside
during trends by design, psar_trend is a pure trend-rider untested in
ranging markets - neither alone covers both regimes, this does.

An open position always finishes out under whichever sub-strategy opened
it (its own SL/TP/trailing logic), even if the regime flips while it's
open - avoids extra exits/fees from reacting to every regime flip, and
avoids cutting a trend-follow position short right as PSAR would trail
it further. Position.strategy_tag records which leg opened it so
exit_decision can route back to the same sub-strategy.
"""

from typing import Optional

from ..orders import effective_fee_pct, fee_gate_passes
from .base import EntryPlan, Position, Strategy
from .mean_reversion import MeanReversionStrategy
from .psar_trend import PSARTrendStrategy


class RegimeSwitchStrategy(Strategy):
    name = "regime_switch"
    # Routing, not a single edge - the range leg (mean_reversion) needs
    # its own fee gate, the trend leg (psar_trend) doesn't. Applied
    # manually inside entry_plan instead of via this class-level flag,
    # which the engines would otherwise apply uniformly to both legs.
    fee_gate_enabled = False

    def __init__(self, params: dict):
        super().__init__(params)
        self.mr = MeanReversionStrategy(params["_mean_reversion"])
        self.psar = PSARTrendStrategy(params["_psar_trend"])
        fees = params["_fees"]
        self._maker_fee_pct = effective_fee_pct(fees["maker_fee_pct"], fees["gst_pct"])
        self._taker_fee_pct = effective_fee_pct(fees["taker_fee_pct"], fees["gst_pct"])
        self._min_profit_to_fee_ratio = self.mr.params.get("min_profit_to_fee_ratio", 0)

    def min_candles_needed(self) -> int:
        return max(self.mr.min_candles_needed(), self.psar.min_candles_needed())

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        mr_ind = self.mr.compute_indicators(candles)
        psar_ind = self.psar.compute_indicators(candles)
        if mr_ind is None or psar_ind is None:
            return None
        regime = "trend" if mr_ind["is_trending"] else "range"
        return {"regime": regime, "mr": mr_ind, "psar": psar_ind}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None:
            return None

        if indicators["regime"] == "trend":
            plan = self.psar.entry_plan(price, candle, indicators["psar"])
            if plan is None:
                return None
            plan.strategy_tag = "trend"
            return plan

        plan = self.mr.entry_plan(price, candle, indicators["mr"])
        if plan is None:
            return None
        if not fee_gate_passes(plan, self._maker_fee_pct, self._taker_fee_pct,
                                self._min_profit_to_fee_ratio):
            return None
        plan.strategy_tag = "range"
        return plan

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        if indicators is None:
            return None
        if position.strategy_tag == "trend":
            return self.psar.exit_decision(position, candle, indicators["psar"], bars_held)
        return self.mr.exit_decision(position, candle, indicators["mr"], bars_held)

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up, not enough candle history yet"
        regime = indicators["regime"]
        if regime == "trend":
            return f"price={price:.2f} regime=TREND | {self.psar.status_line(price, indicators['psar'])}"
        return f"price={price:.2f} regime=RANGE | {self.mr.status_line(price, indicators['mr'])}"
