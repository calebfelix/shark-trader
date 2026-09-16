"""
Manual, one-off verification that this project's order-placement code
actually works against the LIVE Shark Exchange API - not a dry run.

WHAT THIS DOES:
  1. Places ONE real LIMIT BUY order, priced below current market by
     ~80% of the exchange's own limitPriceVarAllowed for the symbol
     (read live from /v1/exchange/exchangeInfo - hardcoding a guess like
     an earlier version's 40% got rejected with error 3009 "Limit price
     is out of allowed values": BTCUSDT only allows +/-15%) - far enough
     that it shouldn't fill, close enough to stay inside the exchange's
     own limit.
  2. Confirms the order appears in your open orders.
  3. Immediately cancels it.
  4. Confirms the cancellation worked.

NOTE (2026-09-15): does NOT call set_leverage. Confirmed (after fixing
the signature bugs below, so this isn't a signing artifact) that
POST /v1/exchange/update/preference fails with error 2314 "Cross margin
is not enabled" for every symbol/margin-mode combination tried, on this
account specifically. If you want a leverage other than whatever's
already configured, raise it directly in the Shark Exchange app/website
- the API can't do it right now. This script uses whatever leverage is
already set (shown as an assumption below, for the margin estimate
only).

FIXED THIS SESSION (see CLAUDE.md for full detail) - two real signature
bugs meant NOTHING signed ever worked correctly before now:
  - requests.post(json=params) re-serializes the body with different
    spacing than what we signed - signed and sent bytes never matched.
  - This backend is Node.js (docs' own signing example is JS's
    JSON.stringify) - JS renders whole-number floats without a decimal
    point (46101, not 46101.0), Python's json.dumps does the opposite.
    A price/quantity that happened to be a whole number broke signing.

WHAT THIS IS NOT:
  - Not a fill test. If price somehow gaps down through the limit price
    in the few seconds this script runs, it COULD fill for real. That
    is why the price is set so far from market, and why you should run
    this when you can watch it happen, not walk away.
  - Not run by an AI assistant. This script requires you to type "yes"
    to proceed, uses your real SHARK_API_KEY/SHARK_API_SECRET from
    .env, and is meant to be run BY YOU in your own terminal after
    reading it.

REQUIREMENTS:
  - .env filled in with real SHARK_API_KEY / SHARK_API_SECRET.
  - Enough INR in your futures wallet to cover margin for the smallest
    order that clears BTCUSDT's min_notional at the leverage below
    (this script prints the exact numbers before asking you to confirm).

Usage:
  python verify_order_endpoint.py
"""

import math
import sys

from shark.config import load_config
from shark.exchange import ExchangeClient
from shark.orders import OrderExecutor

SYMBOL = "XAUINR"
ASSUMED_LEVERAGE = 30   # unknown actual account value - set_leverage is blocked (see note
                       # above). Used only for the pre-flight margin estimate shown to you.
SAFETY_MARGIN = 0.8    # use this fraction of the exchange's own max allowed price
                       # deviation, staying safely inside the limit rather than right at it


def main():
    config = load_config("config.json")
    if not config["api_key"] or not config["api_secret"]:
        sys.exit("SHARK_API_KEY / SHARK_API_SECRET are not set. Fill in .env first (see .env.example).")

    exchange = ExchangeClient(config["base_url"], config["api_key"], config["api_secret"])

    info = exchange.get_exchange_info(SYMBOL)
    last_price = exchange.get_last_price(SYMBOL)
    price_offset_pct = info["limit_price_var_allowed"] * SAFETY_MARGIN * 100
    limit_price = round(last_price * (1 - price_offset_pct / 100.0), 0)

    step = 10 ** info["quantity_precision"]
    qty = max(info["min_notional"] / limit_price, info["min_qty"])
    qty = math.ceil(qty * step) / step  # round UP so notional still clears the minimum
    notional_quote = qty * limit_price
    margin_required_quote_assumed = notional_quote / ASSUMED_LEVERAGE

    inr_price = exchange.get_last_price(f"{info['base_asset']}INR")
    quote_to_inr_rate = inr_price / last_price
    margin_required_inr_assumed = margin_required_quote_assumed * quote_to_inr_rate

    print(f"Symbol: {SYMBOL} | last price: {last_price} {info['quote_asset']} | "
          f"exchange allows limit orders within {info['limit_price_var_allowed']*100:.1f}% of market")
    print(f"Will place LIMIT BUY qty={qty} @ {limit_price} {info['quote_asset']} "
          f"({price_offset_pct:.1f}% below market - should NOT fill)")
    print(f"Notional: {notional_quote:.2f} {info['quote_asset']}")
    print(f"NOT setting leverage - using whatever is already configured on your account "
          f"(raise it in the app if needed - the API is blocked, see note above). "
          f"IF it happens to be {ASSUMED_LEVERAGE}x, margin would be ~{margin_required_inr_assumed:.2f} INR; "
          f"at lower leverage it needs more. Your account_capital in config.json: "
          f"{config['account_capital']} INR.")

    print("\nThis will place ONE REAL ORDER on your live Shark Exchange account, then cancel it.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted - nothing was sent.")
        return

    executor = OrderExecutor(exchange, SYMBOL, dry_run=False)
    print(f"\nPlacing LIMIT BUY {qty} {SYMBOL} @ {limit_price}...")
    order_resp = executor.place_limit_order("BUY", qty, limit_price, reduce_only=False)
    print("place_limit_order response:", order_resp)

    client_order_id = order_resp.get("clientOrderId")
    if not client_order_id:
        print("\nNo clientOrderId in the response - can't confirm/cancel automatically. "
              "Check your Shark Exchange account/app for any open order and cancel it manually.")
        return

    try:
        print(f"\nFetching open orders to confirm {client_order_id} is resting...")
        open_orders = exchange.get_open_orders(SYMBOL)
        print("open_orders response:", open_orders)
    finally:
        print(f"\nCancelling order {client_order_id}...")
        cancel_resp = exchange.cancel_order(client_order_id)
        print("cancel_order response:", cancel_resp)

    print("\nDone. Verify in your Shark Exchange account/app that no order is left open "
          "and your margin has been released before trusting this and moving on.")


if __name__ == "__main__":
    main()
