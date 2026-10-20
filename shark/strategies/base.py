"""Strategy interface. Each strategy owns only its signal logic - the
engines (backtest_engine.py, live_engine.py) own execution, risk sizing,
fees, and reporting so that logic isn't duplicated per strategy.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    entry_price: float
    qty: float
    entry_notional: float
    stop_loss_price: float
    take_profit_price: float
    entry_is_maker: bool
    side: str  # "LONG" or "SHORT"
    opened_at: Optional[int] = None  # candle index at open; set by backtest_engine only
    position_id: Optional[str] = None   # exchange positionId (real mode only)
    opened_at_ms: Optional[int] = None  # exchange open time, epoch ms (real mode only)


@dataclass
class EntryPlan:
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    is_maker: bool
    side: str  # "LONG" or "SHORT"


class Strategy:
    name = "base"
    # Whether entries should be blocked unless the take-profit move clears
    # MIN_PROFIT_TO_FEE_RATIO x round-trip fees. Only mean-reversion uses
    # this today - preserved from the original bot's behavior.
    fee_gate_enabled = False

    def __init__(self, params: dict):
        self.params = params

    def min_candles_needed(self) -> int:
        raise NotImplementedError

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        """candles is the full history seen so far (oldest first). Returns
        None if there isn't enough history yet."""
        raise NotImplementedError

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        raise NotImplementedError

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                       bars_held: int) -> Optional[tuple[str, float, bool]]:
        """Returns (exit_reason, exit_price, exit_is_maker) or None."""
        raise NotImplementedError

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        raise NotImplementedError

    def note_indicators(self, indicators: Optional[dict]) -> None:
        """Called once per loop iteration after entry/exit decisions are
        made, so a strategy can remember state (e.g. crossover direction)
        for the next candle. No-op unless a strategy overrides it."""
        pass
