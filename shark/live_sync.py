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
    def __init__(self, exchange, executor, symbol: str, strategy: Strategy, registry):
        self.exchange = exchange
        self.executor = executor
        self.symbol = symbol
        self.strategy = strategy
        self.registry = registry
        self.adopt_source: str | None = None

    def owns(self, ex_pos: dict) -> bool:
        """Is this exchange position one WE opened? Yes if remembered, or if any fill that built it came
        from an order id we placed. A manual (or any foreign) position is never ours."""
        pid = ex_pos.get("position_id")
        if self.registry.owns_position(pid):
            return True
        start = (ex_pos.get("opened_at_ms") or 0) - 60_000
        for t in self.exchange.get_trade_history(self.symbol, start or None):
            if t.get("positionId") == pid and self.registry.owns_order(t.get("clientOrderId")):
                self.registry.add_position(pid)
                return True
        return False

    def position(self) -> dict | None:
        return self.exchange.get_open_position(self.symbol)

    def adopt(self, ex_pos: dict, plan=None) -> Position:
        """Build the bot's Position from what is really open. SL/TP come from the
        entry plan when we placed it, else are recomputed from the strategy's
        configured percentages around the real entry price."""
        entry, side, qty = ex_pos["entry_price"], ex_pos["side"], ex_pos["qty"]
        saved = self.registry.plan_for(ex_pos.get("position_id"))
        if plan is not None:
            sl, tp, maker = plan.stop_loss_price, plan.take_profit_price, plan.is_maker
            self.adopt_source = "plan"
        elif saved is not None:                     # a restart: the levels this position was opened with
            sl, tp, maker = saved["sl"], saved["tp"], True
            self.adopt_source = "saved"
        else:
            sl, tp = self.strategy.recovery_levels(entry, side)
            maker = True
            self.adopt_source = "recomputed"
        self.registry.set_plan(ex_pos.get("position_id"), sl, tp)
        return Position(entry_price=entry, qty=qty, entry_notional=entry * qty,
                        stop_loss_price=sl, take_profit_price=tp,
                        entry_is_maker=maker, side=side,
                        position_id=ex_pos.get("position_id"), opened_at_ms=ex_pos.get("opened_at_ms"))

    def settle(self, position: Position) -> dict | None:
        """Real INR result of a position that just closed, from Shark's trade history + ledger."""
        return settle_position(self.exchange, self.symbol, position.position_id, position.side,
                               position.opened_at_ms)

    def _ours(self) -> list[dict]:
        """Resting orders on the symbol that WE placed - never the owner's manual orders."""
        return [o for o in self.exchange.list_open_orders(self.symbol)
                if self.registry.owns_order(o.get("clientOrderId"))]

    def _stops(self) -> list[dict]:
        return [o for o in self._ours() if _is_stop(o)]

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
        """Cancel every resting order WE placed on the symbol (used once a position is gone)."""
        n = 0
        for o in self._ours():
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
