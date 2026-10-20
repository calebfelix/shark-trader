"""Real results of a closed position, straight from Shark - in INR, nothing estimated.

  * realised P&L ........ trade-history  realizedProfitInMarginAsset  (price P&L only - NO fees)
  * trading fees ........ trade-history  feeInMarginAsset             (already includes GST)
  * fee discounts ....... ledger         FEE_DISCOUNT for this position's trades
  * funding ............. ledger         FUNDING_FEE + GST_ON_FUNDING_FEE while the position was open
  net = realised - fees + discounts + funding

Verified on 2026-10-20: realizedProfit equals the gross price P&L computed from the fills (it does
not include fees), and feeInMarginAsset equals the wallet drop after a fill.
"""

import time
from datetime import datetime

DISCOUNT_WINDOW_MS = 5_000   # a FEE_DISCOUNT row counts for a fill if booked within this long after it
CLOSE_TOL = 0.999   # closing qty must be >= 99.9% of opening qty before we call it settled


def _ms(iso) -> int:
    return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def settle_position(exchange, symbol: str, position_id: str, side: str, opened_at_ms: int,
                    retries: int = 4, wait_seconds: float = 3.0) -> dict | None:
    """The settled result, or None if Shark's data is missing/incomplete (caller falls back to an estimate)."""
    if not position_id or not opened_at_ms:
        return None
    open_side, close_side = ("BUY", "SELL") if side == "LONG" else ("SELL", "BUY")
    for attempt in range(retries):
        try:
            start = opened_at_ms - 60_000
            trades = [t for t in exchange.get_trade_history(symbol, start) if t.get("positionId") == position_id]
            opening = [t for t in trades if t.get("side") == open_side]
            closing = [t for t in trades if t.get("side") == close_side]
            q_open = sum(_num(t.get("quantity")) for t in opening)
            q_close = sum(_num(t.get("quantity")) for t in closing)
            if q_open > 0 and q_close >= q_open * CLOSE_TOL:
                return _build(exchange, symbol, start, opened_at_ms, trades, opening, closing, q_open, q_close)
        except Exception as e:  # network/shape problems -> retry, then estimate
            print(f"    settlement attempt {attempt + 1} failed: {e}")
        if attempt < retries - 1:
            time.sleep(wait_seconds)
    return None


def _build(exchange, symbol, start, opened_at_ms, trades, opening, closing, q_open, q_close) -> dict:
    avg = lambda ts, q: sum(_num(t["price"]) * _num(t["quantity"]) for t in ts) / q
    closed_at_ms = max(_ms(t["time"]) for t in closing)
    ids = {t["id"] for t in trades}
    ledger = exchange.get_transaction_history(symbol, start)
    # FEE_DISCOUNT rows have no tradeId: Shark books them ~70 ms after the fill they discount.
    trade_times = [_ms(t["time"]) for t in trades]
    discounts = sum(_num(x["amount"]) for x in ledger
                    if x.get("type") == "FEE_DISCOUNT"
                    and any(0 <= _ms(x["time"]) - tt <= DISCOUNT_WINDOW_MS for tt in trade_times))
    funding = sum(_num(x["amount"]) for x in ledger
                  if x.get("type") in ("FUNDING_FEE", "GST_ON_FUNDING_FEE")
                  and opened_at_ms <= _ms(x["time"]) <= closed_at_ms)
    realized = sum(_num(t.get("realizedProfitInMarginAsset")) for t in closing)
    fees = sum(_num(t.get("feeInMarginAsset")) for t in trades)
    return {
        "entry_price": avg(opening, q_open), "exit_price": avg(closing, q_close), "qty": q_close,
        "realized_inr": realized, "fees_inr": fees, "discounts_inr": discounts, "funding_inr": funding,
        "net_inr": realized - fees + discounts + funding,
        "entry_role": opening[0].get("role"), "exit_role": closing[-1].get("role"),
        "closed_at_ms": closed_at_ms,
    }
