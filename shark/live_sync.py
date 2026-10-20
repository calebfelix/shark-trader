"""Real-account reconciliation for the live loop: the exchange's own open
position is the source of truth, not the bot's memory. Used only when
dry_run is False. Every method raises on API trouble - callers must treat an
exception as 'state unknown, do nothing this iteration', never as 'flat'."""

import time

from .settlement import settle_position
from .strategies.base import Position, Strategy


def _is_stop(order: dict) -> bool:
    return "STOP" in str(order.get("type", order.get("orderType", ""))).upper()


class ExchangeSync:
    def __init__(self, exchange, executor, symbol: str, strategy: Strategy):
        self.exchange = exchange
        self.executor = executor
        self.symbol = symbol
        self.strategy = strategy

    def position(self) -> dict | None:
        return self.exchange.get_open_position(self.symbol)

    def adopt(self, ex_pos: dict, plan=None) -> Position:
        """Build the bot's Position from what is really open. SL/TP come from the
        entry plan when we placed it, else are recomputed from the strategy's
        configured percentages around the real entry price."""
        entry, side, qty = ex_pos["entry_price"], ex_pos["side"], ex_pos["qty"]
        if plan is not None:
            sl, tp, maker = plan.stop_loss_price, plan.take_profit_price, plan.is_maker
        else:
            p = self.strategy.params
            sgn = 1 if side == "LONG" else -1
            sl = entry * (1 - sgn * p["stop_loss_pct"] / 100.0)
            tp = entry * (1 + sgn * p["take_profit_pct"] / 100.0)
            maker = True
        return Position(entry_price=entry, qty=qty, entry_notional=entry * qty,
                        stop_loss_price=sl, take_profit_price=tp,
                        entry_is_maker=maker, side=side,
                        position_id=ex_pos.get("position_id"), opened_at_ms=ex_pos.get("opened_at_ms"))

    def settle(self, position: Position) -> dict | None:
        """Real INR result of a position that just closed, from Shark's trade history + ledger."""
        return settle_position(self.exchange, self.symbol, position.position_id, position.side,
                               position.opened_at_ms)

    def _stops(self) -> list[dict]:
        return [o for o in self.exchange.list_open_orders(self.symbol) if _is_stop(o)]

    def ensure_stop(self, position: Position) -> str | None:
        """Make sure exactly one resting stop exists. Returns a message if it acted."""
        if self._stops():
            return None
        close_side = "SELL" if position.side == "LONG" else "BUY"
        self.executor.place_stop_market(close_side, position.qty, position.stop_loss_price)
        return (f"Placed exchange stop-loss: {close_side} STOP_MARKET {position.qty} "
                f"@ {position.stop_loss_price:.2f}")

    def cancel_stops(self) -> int:
        n = 0
        for o in self._stops():
            if o.get("clientOrderId"):
                self.exchange.cancel_order(o["clientOrderId"])
                n += 1
        return n

    def cancel_all_resting(self) -> int:
        """Cancel every resting order on the symbol (used once a position is gone)."""
        n = 0
        for o in self.exchange.list_open_orders(self.symbol):
            if o.get("clientOrderId"):
                self.exchange.cancel_order(o["clientOrderId"])
                n += 1
        return n


class Pending:
    """An order we placed and are waiting to see reflected in the exchange position."""

    def __init__(self, client_order_id, **info):
        self.client_order_id = client_order_id
        self.placed_at = time.time()
        self.info = info

    def age(self) -> float:
        return time.time() - self.placed_at
