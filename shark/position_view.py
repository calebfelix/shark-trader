"""Read-only live view of the bot's own open position, with every cost, in INR - for the dashboard.
Never shows or touches a position the bot didn't open (that one belongs to the account owner)."""

from .settlement import DISCOUNT_WINDOW_MS, _ms, _num


def describe_open_position(exchange, registry, symbol: str, taker_fee_pct_eff: float) -> dict:
    pos = exchange.get_open_position(symbol)
    if pos is None:
        return {"open": False}
    pid, start = pos["position_id"], (pos.get("opened_at_ms") or 0) - 60_000
    trades = [t for t in exchange.get_trade_history(symbol, start or None) if t.get("positionId") == pid]
    ours = registry.owns_position(pid) or any(registry.owns_order(t.get("clientOrderId")) for t in trades)
    if not ours:
        # Say only that something else is open on the symbol - no details of the owner's own trade.
        return {"open": False, "foreign_open": True}
    registry.add_position(pid)

    rate = _num(pos.get("settlement_rate")) or 1.0
    last = exchange.get_last_price(symbol)
    sgn = 1 if pos["side"] == "LONG" else -1
    unrealised = sgn * (last - pos["entry_price"]) * pos["qty"] * rate
    entry_fee = sum(_num(t.get("feeInMarginAsset")) for t in trades)
    ledger = exchange.get_transaction_history(symbol, start or None)
    times = [_ms(t["time"]) for t in trades]
    discount = sum(_num(x["amount"]) for x in ledger if x.get("type") == "FEE_DISCOUNT"
                   and any(0 <= _ms(x["time"]) - tt <= DISCOUNT_WINDOW_MS for tt in times))
    funding = sum(_num(x["amount"]) for x in ledger
                  if x.get("type") in ("FUNDING_FEE", "GST_ON_FUNDING_FEE") and _ms(x["time"]) >= pos["opened_at_ms"])
    est_exit_fee = last * pos["qty"] * rate * taker_fee_pct_eff / 100.0
    stops = [o for o in exchange.list_open_orders(symbol)
             if registry.owns_order(o.get("clientOrderId")) and "STOP" in str(o.get("type", "")).upper()]
    return {
        "open": True, "side": pos["side"], "qty": pos["qty"], "entry_price": pos["entry_price"],
        "last_price": last, "unrealised_inr": unrealised, "entry_fee_inr": entry_fee,
        "entry_discount_inr": discount, "funding_inr": funding, "est_exit_fee_inr": est_exit_fee,
        "net_if_closed_now_inr": unrealised - entry_fee + discount + funding - est_exit_fee,
        "liquidation_price": pos.get("liquidation_price"), "leverage": pos.get("leverage"),
        "margin_inr": pos.get("margin_inr"),
        "stop_price": _num(stops[0].get("stopPrice")) if stops else None,
        "stop_on_exchange": bool(stops), "opened_at_ms": pos["opened_at_ms"],
    }
