"""
One-off diagnostic for the "Cross margin is not enabled" (error 2314)
issue on POST /v1/exchange/update/preference - see CLAUDE.md.

Tries a few variations of the set_leverage call (no order placement at
all - this only touches the account's per-symbol leverage/margin
preference, which is easily changed again later) to narrow down whether
the problem is specific to BTCUSDT, to a particular margin mode, or
account-wide.

Requires .env filled in with real SHARK_API_KEY/SHARK_API_SECRET.
Meant to be run by you, not automated.
"""

from shark.config import load_config
from shark.exchange import ExchangeClient

config = load_config("config.json")
if not config["api_key"] or not config["api_secret"]:
    raise SystemExit("SHARK_API_KEY / SHARK_API_SECRET are not set in .env.")

exchange = ExchangeClient(config["base_url"], config["api_key"], config["api_secret"])

trials = [
    ("BTCUSDT", "ISOLATED", 5),
    ("BTCUSDT", "CROSS", 5),
    ("BTCINR", "ISOLATED", 5),
]

print("This will attempt a few real (but order-free) leverage/margin-mode "
      "preference updates on your account, to narrow down the error.")
confirm = input("Type 'yes' to proceed: ").strip().lower()
if confirm != "yes":
    print("Aborted.")
    raise SystemExit()

for symbol, mode, leverage in trials:
    print(f"\n--- {symbol} / {mode} / {leverage}x ---")
    try:
        resp = exchange.set_leverage(symbol, leverage, mode)
        print("SUCCESS:", resp)
    except Exception as e:
        print("FAILED:", e)
