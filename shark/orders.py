"""Fee-aware order placement, shared by every strategy's live engine run.

DRY_RUN never places a real order - it just prints what would happen.
"""

from .exchange import ExchangeClient


def effective_fee_pct(raw_fee_pct: float, gst_pct: float) -> float:
    """Shark Exchange applies GST on top of the headline fee rate."""
    return raw_fee_pct * (1 + gst_pct / 100.0)


def round_trip_fee_estimate(maker_fee_pct: float, taker_fee_pct: float,
                             entry_is_maker: bool, exit_is_maker: bool) -> float:
    """Returns estimated round-trip fee cost as a % of notional. Fee args
    should already be GST-inclusive (see effective_fee_pct)."""
    entry_fee = maker_fee_pct if entry_is_maker else taker_fee_pct
    exit_fee = maker_fee_pct if exit_is_maker else taker_fee_pct
    return entry_fee + exit_fee


def fee_gate_passes(plan, maker_fee_pct: float, taker_fee_pct: float,
                     min_profit_to_fee_ratio: float) -> bool:
    """Is the planned take-profit move worth enough multiples of round-trip
    fees? abs() makes this correct for both LONG (take_profit > entry) and
    SHORT (take_profit < entry) plans - shared by both engines so the check
    can't drift out of sync between them."""
    implied_move_pct = abs(plan.take_profit_price - plan.entry_price) / plan.entry_price * 100
    fee_cost_pct = round_trip_fee_estimate(maker_fee_pct, taker_fee_pct,
                                            entry_is_maker=True, exit_is_maker=True)
    return implied_move_pct >= fee_cost_pct * min_profit_to_fee_ratio


class OrderExecutor:
    def __init__(self, exchange: ExchangeClient, symbol: str, dry_run: bool):
        self.exchange = exchange
        self.symbol = symbol
        self.dry_run = dry_run

    def place_limit_order(self, side: str, quantity: float, price: float, reduce_only: bool) -> dict:
        """Used for entries/exits intended to fill as MAKER (cheaper).
        reduce_only must be False for an opening order, True for a closing
        order - required (no default) so every call site states its intent
        explicitly, since a wrong value could flip a position to the
        opposite side instead of closing it."""
        params = {
            "placeType": "ORDER_FORM",
            "quantity": round(quantity, 6),
            "side": side,
            "symbol": self.symbol,
            "type": "LIMIT",
            "price": round(price, 2),
            "reduceOnly": reduce_only,
            "marginAsset": "INR",
        }
        if self.dry_run:
            print(f"[DRY RUN] Would place {side} LIMIT order for {params['quantity']} "
                  f"{self.symbol} @ {params['price']} reduceOnly={reduce_only} (targeting MAKER fee)")
            return {"dry_run": True, **params}
        return self.exchange.signed_post("/v1/order/place-order", params)

    def place_market_order(self, side: str, quantity: float, reduce_only: bool) -> dict:
        """Used for time-critical exits (e.g. stop-loss) - immediate TAKER fill."""
        params = {
            "placeType": "ORDER_FORM",
            "quantity": round(quantity, 6),
            "side": side,
            "symbol": self.symbol,
            "type": "MARKET",
            "reduceOnly": reduce_only,
            "marginAsset": "INR",
        }
        if self.dry_run:
            print(f"[DRY RUN] Would place {side} MARKET order for {params['quantity']} "
                  f"{self.symbol} reduceOnly={reduce_only} (TAKER fee, immediate exit)")
            return {"dry_run": True, **params}
        return self.exchange.signed_post("/v1/order/place-order", params)

    def place_stop_market(self, side: str, quantity: float, stop_price: float) -> dict:
        """Reduce-only STOP_MARKET resting on the exchange: closes (never flips)
        the position if price reaches stop_price, even when this bot is down.
        Request shape from docs.sharkexchange.in (stopPrice is required for
        STOP_MARKET); not yet exercised against the live endpoint."""
        params = {
            "placeType": "ORDER_FORM",
            "quantity": round(quantity, 6),
            "side": side,
            "symbol": self.symbol,
            "type": "STOP_MARKET",
            "stopPrice": round(stop_price, 2),
            "reduceOnly": True,
            "marginAsset": "INR",
        }
        if self.dry_run:
            print(f"[DRY RUN] Would place {side} STOP_MARKET for {params['quantity']} "
                  f"{self.symbol} stopPrice={params['stopPrice']} reduceOnly=True")
            return {"dry_run": True, **params}
        return self.exchange.signed_post("/v1/order/place-order", params)
