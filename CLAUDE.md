# CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot)

Context file for Claude (or anyone else) picking up this project without a
prior conversation. Read this first before making changes.

## What this project is

A Python algo-trading project for **Shark Exchange** (sharkexchange.in), an
Indian crypto derivatives (futures/options) exchange with INR settlement.

**Refactored 2026-09-15** from three standalone scripts into a single
entry point + shared package + config file:

```
main.py                    # entry point: python main.py backtest|run --strategy <name>
config.json                 # all tunable settings - no code edits needed
shark/
  config.py                  # loads config.json, layers in API keys from env
  exchange.py                 # ExchangeClient - klines (public) + signed order endpoints
  indicators.py                # bollinger_bands, rsi, atr, sma - pure functions
  risk.py                       # RiskManager - position sizing, daily-loss halt, cooldown
  orders.py                      # OrderExecutor - fee-aware limit/market order placement
  performance.py                  # PerformanceTracker - CSV trade log + running summary
  backtest_engine.py               # strategy-agnostic backtest loop
  live_engine.py                    # strategy-agnostic live/dry-run poll loop
  strategies/
    base.py                          # Strategy interface (Position, EntryPlan dataclasses)
    mean_reversion.py                 # the validated default - BB+RSI+ATR, long-only
    sma.py                             # trend-following crossover, kept for reference, long-only
    scalp.py                           # fast BB+RSI on 1m, long+short, untuned - see below
```

Run with:
```
python main.py backtest --strategy mean_reversion
python main.py run --strategy mean_reversion
```
See `README.md` for full usage. This file is about *why* things are
built the way they are, so future changes stay consistent.

## Test suite (2026-09-16)

`tests/` (pytest, `requirements-dev.txt`) covers the pure logic most
likely to cause a real loss if silently broken: indicator math
(`test_indicators.py`), position sizing/risk halts
(`test_risk.py`), fee gating (`test_orders.py`), P&L sign and the
backtest loop (`test_backtest_engine.py`), every strategy's entry/exit
decisions (`test_strategies.py`), and HMAC signing/JS-number-safety
(`test_exchange_signing.py`) - the last one pure logic only, no test
here ever calls the live API. Several tests are direct regressions for
real bugs found earlier in this project (the psar_trend sizing/exit
decoupling bug, the SHORT fee-gate sign bug, the JS whole-number-float
signing bug, the min-notional round-up-not-down bug) - see each bug's
own CLAUDE.md section for the original incident. Run with:
```
venv\Scripts\python.exe -m pytest tests/ -v
```
Deliberately NOT covered: the live Shark Exchange API itself (that's
`verify_order_endpoint.py` and manual dry-runs), and `main.py`/
`live_engine.py`'s I/O-heavy wiring (better exercised by an actual
dry-run than a mocked poll loop). Add tests here alongside any future
strategy or risk-math change, same as the regression tests already here
were added after their bugs were found - don't wait for the next bug to
start.

### Why this shape

The original three scripts (`shark_backtest.py`, `shark_mean_reversion_bot.py`,
`shark_sma_bot.py` - since removed, this refactor fully superseded them)
each had their own copy of `RiskManager`, the indicator math, and the
exchange-signing helpers — a fix like the klines POST/`pair` fix below
had to be applied three times.
Strategies are now pluggable (`shark/strategies/`, one file per strategy,
just the signal logic — entry/exit conditions), while the engines
(`backtest_engine.py`, `live_engine.py`) own execution, risk sizing, fees,
and reporting once, shared by every strategy. Adding a new strategy means
writing one file that implements `Strategy` (see `strategies/base.py`) and
registering it in `strategies/__init__.py`'s `STRATEGIES` dict — no
changes to config loading, risk management, order placement, or
reporting.

Each strategy still trades exactly as its original script did — the
refactor did not homogenize behavior: `mean_reversion` still prefers
maker limit orders with a fee gate, `sma` still uses plain market orders
with no fee gate. The one deliberate behavior addition: `sma` now gets
a backtester and `PerformanceTracker` logging, neither of which it had
before (previously backtesting only covered mean-reversion, and only
mean-reversion logged trades to CSV).

## User's goals (established across the conversation that built this)

- Wants **consistent, lower-but-steady** returns over big/volatile swings —
  explicitly said "profits may be low but at least consistent."
- This is why the bot uses **mean reversion** (Bollinger Bands + RSI), not
  the trend-following SMA crossover — trend-following has a lower win rate
  with occasional big wins, which is the opposite risk profile from what
  was asked for. Don't default back to trend-following without checking
  this preference still holds.
- Wants **fees factored into strategy decisions**, not just calculated
  after the fact. The bot actively skips trades whose take-profit target
  doesn't clear estimated round-trip fees by a healthy margin
  (`MIN_PROFIT_TO_FEE_RATIO`), and prefers maker (limit) orders over taker
  (market) orders wherever the trade isn't time-critical.
- Is a beginner-to-intermediate retail trader (started this project asking
  about a ₹1,000 deposit and a fee-refund promo), testing with modest
  capital (~₹5,000 was discussed as a plausible testing pool). Keep
  defaults conservative; don't assume large account sizes.

## Shark Exchange — key facts (verify before relying on these)

- Indian crypto **derivatives** exchange (futures + options), INR-settled,
  operated by Lightningnodes Technologies Private Limited, FIU-registered.
- **Futures fees**: 0.016% maker / 0.040% taker, **+18% GST applied
  separately on top of each fee** (not included in the headline rate).
  Effective: ~0.01888% maker / ~0.04720% taker. Source:
  sharkexchange.in/fee-structure — re-check this page if fee logic changes,
  since Shark Exchange reserves the right to adjust rates.
- INR deposits/withdrawals are free.
- Runs a "30 Day Challenge" / "Trade Crypto for Free" promo: if your net
  realised P&L for a calendar month is positive, trading fees for that
  month are refunded automatically (capped ~₹30,000/month). This is
  separate from the bot project but was the original context that started
  this conversation — the user may reference it again.
- API docs live at **docs.sharkexchange.in**. Base URL used in these
  scripts: `https://api.sharkexchange.in` (`config.json`'s `base_url`).
  Auth is HMAC-SHA256 signing with an API key + secret (see
  `shark/exchange.py`'s `ExchangeClient._sign()` / `signed_get()` /
  `signed_post()`).

## API contract — confirmed against the live endpoint (2026-09-15)

`/v1/market/klines` is **POST**, not GET, and the pair parameter is named
**`pair`**, not `symbol` — the original scripts guessed GET + `symbol` and
got a 404 against the real API. `shark/exchange.py`'s `get_klines()` uses
`requests.post(url, json={"pair": ..., "interval": ..., "limit": ...})`.
Confirmed response shape is a list of dicts: `{"startTime", "open",
"high", "low", "close", "endTime", "volume"}` (all values as strings) —
parsing now assumes this shape directly (the original three scripts'
speculative list-shape `[t,o,h,l,c,v]` fallback was dropped since it's
confirmed never returned). `"5m"`, `"15m"`, `"1h"` are all confirmed
valid `interval` values (live 201 responses); the full enum beyond that
is still unconfirmed.

## Long + short support and the scalp strategy (2026-09-15)

Added a third strategy, `scalp` (`shark/strategies/scalp.py`) - fast/tight
Bollinger Bands + RSI mean-reversion on 1m candles, aimed at frequent,
quick, fee-gated trades. Unlike `mean_reversion`/`sma`, it trades **both
directions**: buy near the lower band when RSI is oversold (long), sell
near the upper band when RSI is overbought (short). This required real
interface changes, not just a new strategy file:

- **`Position`/`EntryPlan`** (`shark/strategies/base.py`) now carry a
  `side: "LONG"|"SHORT"` field. `mean_reversion.py`/`sma.py` were updated
  mechanically to pass `side="LONG"` explicitly - their actual trading
  behavior is unchanged (confirmed via backtest before/after: same trade
  counts and win rates).
- **Both engines are side-aware**: order side flips (LONG opens via BUY/
  closes via SELL; SHORT opens via SELL/closes via BUY) and so does the
  gross P&L sign (LONG: `exit - entry`; SHORT: `entry - exit`).
- **`reduceOnly` is now meaningful and used**: `OrderExecutor.place_limit_order`/
  `place_market_order` take a required `reduce_only: bool` (no default,
  so every call site must state intent). Opens pass `False`, closes pass
  `True` - this matters more now that both directions exist, so a bad
  fill can't accidentally flip a position to the opposite side instead
  of closing it.
- **Fee-gate bug fixed + deduplicated**: the old fee-gate check
  (previously copy-pasted slightly differently in `backtest_engine.py`
  and `live_engine.py`) computed `(take_profit/entry - 1) * 100`, which
  is negative for a SHORT plan (`take_profit < entry`) and would have
  broken the gate. Now one shared `fee_gate_passes()` in `shark/orders.py`
  uses `abs(take_profit - entry)`, imported by both engines.
- **Max-hold-time exit**: `Strategy.exit_decision` gained a `bars_held: int`
  parameter (existing strategies accept and ignore it). `scalp` uses it to
  force-exit (`max_hold_candles` config) if a position hasn't resolved via
  SL/TP/signal - keeps capital turning over, the point of scalping.
  `backtest_engine.py` tracks this exactly via candle index
  (`Position.opened_at`). `live_engine.py` approximates it as a poll-loop
  iteration counter (`bars_since_open`) since there's no natural candle
  index in the live loop - accurate as long as `poll_seconds` roughly
  matches the strategy's `kline_interval` (true for scalp's 60s/1m).

**Scalp's starting config values are untuned** (`bb_period=10`,
`rsi_period=7`, tight SL/TP, `max_hold_candles=15`) - first backtest run
came back net-negative (32% win rate, -7.8%). Same workflow as
mean_reversion's earlier tuning: this needs a parameter sweep before it's
trustworthy, not a working default. Don't treat it as validated.

**Short-selling has never placed a real order** - same caveat as the
rest of the order-placement code (see "Still unverified" below): tested
via backtest math and dry-run print-checking (`reduceOnly`/side/P&L sign
all verified programmatically), never against the live endpoint.

## ⚠️ CRITICAL: minNotional makes small accounts untradeable as-is (2026-09-15)

Confirmed live via the public `/v1/exchange/exchangeInfo` endpoint (not
previously used anywhere in this project - worth keeping in mind for
future checks). Per-contract order minimums, from the `filters` array:

| Contract | minQty | MIN_NOTIONAL | maxLeverage | quantityPrecision |
|----------|--------|--------------|-------------|--------------------|
| BTCUSDT  | 0.001  | 115 (USDT)   | 150         | 3                  |
| BTCINR   | 0.001  | 10988.25 (INR) | 150       | 3                  |
| XAUINR   | 0.001  | 549.125 (INR)  | 75        | 3                  |

**The codebase has no concept of any of this** - `shark/risk.py`'s
`calc_position_size()` sizes purely off `risk_per_trade_pct` x
`account_capital` / stop-distance, with no leverage and no min-notional
check. `shark/orders.py`'s order params have no `leverage` field at all.
On a small account this silently produces orders the exchange will
reject: at `account_capital=610.49` (a real balance the user reported),
computed notional was ~₹407 (~$4.62) - nowhere near BTCUSDT's 115 USDT
(~₹10,100) minimum.

**Getting a real order to clear the minimum on a ₹610 account requires
~16-17x leverage** - which breaks the entire point of the risk-per-trade
config: at 16x leverage, a 1.5% stop-loss costs ~24% of capital in one
trade, not the intended 1%. This is a genuine, unresolved conflict
between the user's actual account size and this strategy's risk model as
currently built - not something to silently patch by adding a leverage
multiplier without the user explicitly choosing to accept that risk
tradeoff.

**Before this project can be used live with a small account**, one of
these needs to happen (user's call, not an automatic fix):
- Fund the account well above the min-notional threshold so 1x/low
  leverage naturally clears it (e.g. well over ₹10,100 for BTCUSDT at
  1x), keeping the existing risk-per-trade math meaningful, OR
- Explicitly redesign position sizing to be leverage-aware (add a
  `leverage` field to `config.json` and the order params, compute
  margin-based sizing, and make the user consciously choose a leverage
  level with eyes open about the resulting real risk-per-trade), OR
- Trade a lower-min-notional contract if one exists that still fits the
  strategy (XAUINR's ₹549 minimum is far more reachable at this capital
  level, though gold backtested poorly for mean_reversion earlier this
  session - would need its own retune).

**Update - implemented leverage-aware sizing (2026-09-15, same day)**:
user chose to make sizing leverage-aware rather than fund the account
further or switch symbols. Implemented in `shark/risk.py`
(`calc_leveraged_position_size`, `RiskManager`), `shark/exchange.py`
(`get_exchange_info`, `get_last_price`, `set_leverage`), and wired up in
`main.py`'s `cmd_run`. `config.json` gained a top-level `leverage` block
(`value`, `margin_mode`, `allow_min_notional_override`).

**Critical bug caught and fixed during implementation**: the first
version compared `account_capital` (always INR) directly against
`min_notional`/prices for BTCUSDT (quoted in **USDT**) with no currency
conversion - this would have sized positions ~95x too large (the INR/USDT
rate) had it ever run against real capital. Caught by manually re-deriving
the numbers with a live FX rate before trusting the code, not by the code
itself surfacing an error. Fixed by:
- `get_exchange_info()` now also returns `base_asset`/`quote_asset`.
- `main.py`'s `cmd_run` derives an INR/quote-currency rate from the
  exchange's own live prices when the traded symbol isn't INR-quoted -
  e.g. for `BTCUSDT`, it fetches `BTCINR` and `BTCUSDT` last prices and
  divides (`quote_to_inr_rate = inr_price / quote_price`). If no
  matching `<base><INR>` pair exists to derive from, `cmd_run` refuses
  to start (`SystemExit`) rather than guess.
- `calc_leveraged_position_size` takes `quote_to_inr_rate` and does all
  arithmetic in consistent units throughout, returning INR-denominated
  values in its info dict (suffixed `_inr`) so a currency mismatch can't
  silently sneak back in later.

**Second bug caught in the same session**: when `allow_min_notional_override`
forces qty up to clear `min_notional`, rounding it DOWN to the exchange's
`quantityPrecision` (as ordinary risk-based sizing correctly does, to
avoid exceeding intended risk) could truncate it back BELOW min_notional
(e.g. 115 USDT / 76813.60 = 0.0014974 BTC, floors to 0.001 at precision 3,
whose notional 76.81 USDT is under the 115 minimum again). Fixed: qty is
rounded UP instead, specifically in the forced-by-min-notional branch.

**Verified via direct function calls** (not yet via a real order):
override-disabled correctly refuses and reports the real INR notional
vs. the real INR-converted minimum; override-enabled at insufficient
leverage correctly refuses with the margin shortfall; override-enabled
at sufficient leverage correctly sizes up and reports the honest
`risk_effective_pct` (e.g. 36% of capital on one trade at 30x leverage -
nowhere near the intended 1%, and exactly the number a user needs to see
before opting into this).

**Still true**: none of this has been tested against a real order. It's
been verified as internally consistent math, not verified against how
the exchange actually behaves when an order this small/large is placed.

### `verify_order_endpoint.py` - manual real-order verification tool

A standalone script (not part of the strategy/engine code, not wired
into `main.py`) for manually checking the order-placement/cancel
endpoints against the LIVE API without betting on price movement: sets
leverage, places one real LIMIT BUY deliberately priced ~40% below
market (so it rests instead of filling), confirms it via open-orders,
cancels it. Requires real `.env` keys and an explicit typed "yes"
confirmation - meant to be run by the user in their own terminal, not
automated. Added because the user asked to test with real money on a
~₹610 balance that can't support the actual strategy within its
intended 1% risk - this is a safer way to verify the code/API contract
than either a real market order or running the bot live at a distorted
risk level. Uses `ExchangeClient.set_leverage`/`get_last_price`/
`cancel_order`/`get_open_orders` - all added this session.

**Run against the real account (2026-09-15) - findings so far:**

1. **Clock drift caused a real failure**: first attempt hit
   `{"error":"4008","details":"Timestamp older than 5 seconds"}` -
   the local machine's clock was ~6.4s behind Shark Exchange's server
   time (confirmed by comparing local time to the `Date` header on a
   plain GET to `/v1/exchange/exchangeInfo` - no external time API
   needed). Fixed via Windows time resync (`w32tm /resync /force` as
   admin, or Settings > Time & Language > Date & Time > Sync now).
   **Lesson for whoever hits `error 4008` again**: check clock drift
   this same way before assuming it's a code/signing bug.
2. **`POST /v1/exchange/update/preference` fails identically for both
   `marginMode: "ISOLATED"` and `marginMode: "CROSS"`**, both times with
   `{"error":"2314","details":"Cross margin is not enabled"}`. Since the
   error is the same regardless of which mode is requested, this isn't
   really about ISOLATED vs CROSS - margin/leverage trading itself
   appears to need a one-time enablement on the account, done through
   Shark Exchange's own web/app UI (a risk disclosure or "enable futures
   trading" step), not exposed via any documented API endpoint. No
   read-only endpoint exists to confirm this directly (checked
   `GET /v1/positions/OPEN` - empty, as expected, doesn't reveal margin
   defaults). User is checking the Shark Exchange UI directly as the
   next step - update this note once resolved.
3. Order placement/cancellation has still never been reached or tested -
   blocked on the leverage-setting step above.
4b. **Root cause found and fixed (2026-09-15, same day)**: `place-order`
   returned `{"error":"4007","details":"Signature mismatch"}`. Root
   cause: `signed_post`/`signed_delete` signed a COMPACT JSON string
   (`json.dumps(params, separators=(",", ":"))`, matching the docs'
   exact spec) but then sent the request via `requests.post(url,
   json=params, ...)` - which lets `requests` re-serialize the body
   itself using ITS OWN default separators (with spaces). The signed
   string and the actual wire bytes were never the same string.
   Confirmed directly: `json.dumps(params, separators=(',',':'))` !=
   `requests.Request(..., json=params).prepare().body` for identical
   params. Fixed by serializing once and sending that exact string via
   `data=data_to_sign` instead of `json=params`, so the signed bytes and
   sent bytes are guaranteed identical - verified with a prepared-request
   byte comparison after the fix.

   **This casts doubt on the earlier "Cross margin is not enabled"
   diagnosis** - `update/preference` may have been hitting this same
   signature bug the whole time, with error 2314 being whatever the
   server's request pipeline checks before (or instead of) signature
   verification on that specific endpoint, not necessarily a real
   account-side gate. Re-test `diagnose_leverage.py` and
   `verify_order_endpoint.py` now that the signing bug is fixed before
   concluding anything about margin-mode enablement again.

4c. **Byte-match fix alone wasn't enough - second signature bug found and
   fixed (2026-09-15, same day)**: retried `verify_order_endpoint.py`
   after 4b's fix, still got `error 4007 "Signature mismatch"` on
   `place-order` specifically (not on the endpoints without float
   fields). Root cause: the docs' own signing example is JS's
   `JSON.stringify(params)`, meaning this backend is almost certainly
   Node.js and reconstructs the signing string server-side by
   re-parsing/re-serializing the received body (common when body-parsing
   middleware runs before a signature-check step, so raw bytes aren't
   available to hash directly). JS has no int/float type distinction:
   `JSON.stringify(46101.0)` -> `"46101"` (no decimal point), but
   Python's `json.dumps(46101.0)` -> `"46101.0"`. The test order's limit
   price landed on an exact whole number (`46101.0`), triggering exactly
   this mismatch - `update/preference`'s only numeric field is an
   already-integer `leverage`, so it never hit this class of bug.
   Fixed: `ExchangeClient._js_number_safe()` recursively converts any
   whole-number float to a Python `int` before `json.dumps`, applied in
   `signed_get`/`signed_post`/`signed_delete`, so a price/quantity that
   happens to be a whole number serializes the same way JS would.
   Verified directly (`46101.0` -> `"46101"`, `0.0035` stays `"0.0035"`).
   **Confirmed this fixed signing**: retried again and got PAST signature
   validation entirely - a real business-logic error came back instead
   (`error 3009 "Limit price is out of allowed values"`, see 4d) -
   proof the signature is now valid.

4d. **`limitPriceVarAllowed` constraint (2026-09-15, same day)**:
   `exchangeInfo`'s `limitPriceVarAllowed` field (0.15 for BTCUSDT, seen
   but not understood earlier) caps how far a LIMIT order's price may
   deviate from market - our test script's original 40%-below-market
   price was rejected for exceeding it. Fixed: `get_exchange_info()` now
   returns `limit_price_var_allowed`, and `verify_order_endpoint.py`
   computes its test price as 80% of that allowed range (12% for
   BTCUSDT) instead of a hardcoded guess. Not yet confirmed against a
   real order - next attempt will tell us if this was the last blocker.

4e. **Re-enabled `set_leverage` in `verify_order_endpoint.py` (2026-09-15,
   same day)**: after 4d's fix, the order attempt got past the price
   check too and hit `error 3018 "Insufficient margin"` - real, expected,
   since leverage was never actually raised (every set_leverage attempt
   had failed before the signature fixes). Since the byte-mismatch fix
   (4b) applies to every signed POST including `update/preference`, it's
   very possible leverage-setting works now. Re-added the `set_leverage`
   call (30x, ISOLATED) before order placement in the script to test
   this directly rather than via the separate diagnostic script.

4f. **`error 2314` confirmed real, not a signature artifact (2026-09-15,
   same day)**: re-tried `set_leverage` after both signature fixes (4b,
   4c) - still fails identically with `"Cross margin is not enabled"`.
   Since `place-order`'s signature is now provably correct (it gets past
   auth to real business errors - 3009 price, 3018 margin), and
   `update/preference` still fails the exact same way it always has,
   this endpoint's rejection is a genuine account-side restriction, not
   something our code can work around. **The API cannot change leverage
   on this account right now - it has to be done in the Shark Exchange
   app/website directly** (told to the user; unconfirmed whether doing
   so resolves it). Reverted `verify_order_endpoint.py` to skip
   `set_leverage` again (matches 4a's version) - it uses whatever
   leverage is already configured on the account.

4g. **SUCCESS (2026-09-15, same day) - order placement fully verified
   against the live API.** `verify_order_endpoint.py` placed a real
   LIMIT BUY (id `37727015`, 0.002 BTCUSDT @ 67610, `leverage: 30` in
   the response - the account's leverage WAS already 30x, explaining why
   this attempt had enough margin where an earlier one didn't), confirmed
   it resting unfilled via `open-orders`, and cancelled it cleanly
   (`status: CANCELED, success: True`). This proves, against the real
   API, not just internally-consistent code: auth/HMAC signing, the
   place-order request schema (`placeType`, `marginAsset: "INR"` even on
   a USDT pair, `reduceOnly`, etc. - all previously doc-verified only),
   margin locking, `open-orders`, and `cancel-order` all work correctly
   end-to-end. The three real bugs found and fixed this session (4b
   byte-mismatch, 4c JS number formatting, 4d limitPriceVarAllowed) were
   all genuine - not overcautious guessing.

   **Still open / not yet verified**: `set_leverage` remains blocked by
   `error 2314` for this account (cause unknown - not further
   investigated once order placement itself was confirmed working with
   the account's existing 30x). A MARKET order has never been tested
   (only LIMIT). No order has ever been allowed to actually fill.
   `PRICE_OFFSET_PCT`/quantity-precision edge cases beyond what this one
   test exercised are unverified. Treat this as "the pipe works," not
   "every code path through it is proven."
4. **Isolated via `diagnose_leverage.py` (new, one-off diagnostic script,
   not part of the main bot)**: tried `BTCUSDT`/ISOLATED,
   `BTCUSDT`/CROSS, `BTCINR`/ISOLATED - **all three failed identically**
   with the same `2314 "Cross margin is not enabled"` error. Rules out
   anything specific to BTCUSDT or to a particular margin mode - this is
   account-wide. The user's app UI shows ISOLATED already set for the
   account, yet the API rejects every preference update attempt - a
   backend/account-flag issue, not something diagnosable further from
   the client side. **Next step is Shark Exchange support**, not more
   code changes - see the exact repro details to hand them above this
   note in the conversation this was found in, or just re-run
   `diagnose_leverage.py` to reproduce on demand.

## marginAsset is always "INR", even on non-INR pairs (2026-09-15)

Confirmed via docs.sharkexchange.in: `marginAsset: "INR"` is used in the
docs' own examples for both `BTCINR` and `BTCUSDT`. The exchange appears
to be INR-margined platform-wide regardless of a pair's quote currency,
so `shark/orders.py`'s hardcoded `"marginAsset": "INR"` on every order
is correct as-is — don't "fix" it to match the pair's quote currency
(e.g. `USDT` for `BTCUSDT`) without checking with the user first, since
the docs suggest that would be wrong. Not live-order-tested though (see
below) - if a real order on a non-INR pair ever gets a 400 mentioning
marginAsset, that's the first thing to revisit.

## ⚠️ Still unverified — check before going live

- The exact `/v1/order/place-order` request schema. docs.sharkexchange.in
  confirms the field names used in the scripts (`placeType`,
  `marginAsset`, `reduceOnly`, `type` one of MARKET/LIMIT/STOP_MARKET/
  STOP_LIMIT) but this has **not** been tested against the live endpoint
  (that would place a real order) — treat it as doc-verified, not
  live-verified.
- Full enum of supported `interval` values beyond `"5m"`/`"15m"`/`"1h"`
  (all three accepted a POST and returned data, so likely a fairly
  standard set).

## Retuned against a longer, honest window (2026-09-15, supersedes the section below)

The original tuning below (12 trades / 83.3% win rate / +2.61%) was
checked on a longer window and **did not hold up** — over 4500 candles
(~47 days) at the same `sl=1.0`/`tp=1.5`, it dropped to 50 trades / 66.0%
win rate / **net -2.03%** (stop-losses jumped from 1 to 15). The original
result was a lucky short stretch, not a validated edge. CLAUDE.md's own
caveat at the time said as much ("not enough to call this validated") -
this is that caveat cashing in.

Re-swept `stop_loss_pct` x `take_profit_pct` x `atr_trend_threshold_pct`
x `kline_interval` (15m, 1h) against ~60 days of data per interval
(paginated via `ExchangeClient.get_klines`'s new endTime pagination -
see below). `atr_trend_threshold_pct` turned out to have zero effect
across 0.8-10.0 in this window (confirmed not a bug: BTCUSDT 15m ATR%
rarely exceeds 0.8% in this data, so anything above that is non-binding -
verified by testing an extreme 0.3 threshold, which did reduce trade
count as expected). Best result requiring >=15 trades (to avoid
cherry-picking a config that barely fired):

| interval | SL%  | TP% | trades | win rate | net P&L | max DD | profit factor |
|----------|------|-----|--------|----------|---------|--------|----------------|
| **15m**  | **1.5** | **2.0** | **56** | **67.9%** | **+2.83%** | **3.54%** | **1.30** |
| 1h       | 0.8  | 2.0 | 19     | 47.4%    | +3.40%  | 5.30%  | 1.31 |

Chose 15m/1.5/2.0 over the 1h option despite similar net return - much
higher win rate (68% vs 47%) and lower drawdown (3.5% vs 5.3%), which
fits the user's stated preference for consistency over volatility.
Confirmed again via the actual CLI (`config.json`'s default 4500-candle
lookback, not the sweep's 5760): 45 trades, 73.3% win rate, +2.85% net,
6 stop-losses (down from 12 at the old SL/TP).

`config.json`'s `strategies.mean_reversion.stop_loss_pct`/`take_profit_pct`
are now `1.5`/`2.0` (was `1.0`/`1.5`). `backtest.lookback_candles` is
now `4500` (was `1000`) - the short window was part of why the original
tuning didn't hold up, so the longer default should stay unless there's
a specific reason to shrink it back.

**Still true**: this is one longer backtest on one historical window, not
proof the edge persists into future/different market regimes. Re-check
periodically, and dry-run before trusting it with real money.

### Pagination (2026-09-15)

`ExchangeClient.get_klines()` now paginates automatically when `limit`
exceeds the API's per-call cap (confirmed 1500 candles, regardless of
what `limit` is requested) - it walks backward through history via the
`endTime` param, using each page's oldest candle's `startTime` as the
next page's cutoff, stopping early if the exchange runs out of history.
No caller-facing change - `get_klines(symbol, interval, limit=5760)`
just works now instead of silently truncating to 1500.

## Timeframe tuning (2026-09-15, superseded by the section above - kept for history)

Swept `KLINE_INTERVAL` x `TAKE_PROFIT_PCT` against live klines
(`BTCINR`, 1000-candle lookback for 5m/15m, 500 for 1h; see the sweep
approach in git history / ask to regenerate). Results:

| interval | TP%  | trades | win rate | net P&L | max DD |
|----------|------|--------|----------|---------|--------|
| 5m       | 1.2  | 7      | 57.1%    | -0.61%  | 1.07%  |
| 5m       | 2.0  | 7      | 57.1%    | -0.61%  | 1.07%  |
| **15m**  | **1.5** | **12** | **83.3%** | **+2.61%** | **1.07%** |
| 15m      | 2.5  | 12     | 83.3%    | +2.61%  | 1.07%  |
| 1h       | 3.0  | 7      | 28.6%    | -3.24%  | 5.04%  |

Switched `config.json`'s `strategies.mean_reversion` to
`kline_interval = "15m"`, `take_profit_pct = 1.5`. Widening TP at 15m
made no difference — most winning exits trigger on the RSI-overbought
signal before reaching TP, so a wider TP is just unused headroom.

1h did worst — `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`, and especially
`ATR_TREND_THRESHOLD_PCT` are fixed values tuned implicitly for
5m/15m noise levels, not adjusted for the wider price swings a 1h
candle naturally has. If 1h is worth revisiting, those need their own
tuning pass rather than reusing the 5m/15m values.

**Caveat**: 12 trades over ~10 days of 15m candles is still a small
sample — enough to justify switching the default, not enough to call
this validated. Re-run the sweep periodically with more history/across
regimes before trusting it further, and keep dry-running before any
`DRY_RUN = False` decision.

## Design decisions worth preserving

- **Mean reversion over trend-following**, per the user's stated goals
  above.
- **Maker orders for entries and take-profit, market orders only for
  stop-loss.** Stop-loss uses market/taker deliberately — the reasoning
  given to the user was that saving ~0.03% on fees isn't worth the risk of
  a limit order not filling while a losing position keeps moving further
  against you.
- **Fee-vs-edge gate before every entry** (`MIN_PROFIT_TO_FEE_RATIO`,
  default 3.0×). This was specifically requested — don't remove it without
  checking with the user, since it's the mechanism that keeps a
  high-win-rate/small-profit strategy from being quietly eaten by costs.
- **ATR-based trend filter** — mean reversion stands aside when the market
  is judged to be trending (`ATR_TREND_THRESHOLD_PCT`), since mean
  reversion's known failure mode is fighting a real trend ("picking up
  pennies in front of a steamroller," as explained to the user).
- **Risk management framework** (`shark/risk.py`, shared by every
  strategy): position sizing as a fixed % of capital risked per trade
  (not fixed quantity), hard stop-loss/take-profit, a daily max-loss
  circuit breaker, and a consecutive-loss cooldown. New strategies get
  this for free through `live_engine.py` — no need to reimplement it.
- **Performance tracking** (`shark/performance.py`, shared by every
  strategy — previously only the mean-reversion bot had this): every
  closed trade logs to `shark_bot_trade_log_<strategy>.csv`, with a
  running console summary after each trade and a final summary on
  shutdown (`atexit` + SIGINT/SIGTERM handlers in `main.py`).
- **Backtest engine only uses `RiskManager.position_size()` math, not
  the daily-loss/cooldown halts** (`shark/backtest_engine.py` calls
  `risk.calc_position_size()` directly rather than instantiating
  `RiskManager`). Those halts are wall-clock (`date.today()`) based and
  don't translate onto a backtest that fast-forwards through many real
  days in seconds — wiring them in would silently make the daily-loss
  halt apply to the entire backtest as one "day," understating how many
  trades the strategy would actually take. If that gap ever matters,
  it needs its own design (simulating day rollover from candle
  timestamps), not a quick wire-up.

## Safety/scope notes

- `"dry_run": true` in `config.json` is the default. Never flip this to
  `false` as part of an automated change — that's a real-money decision
  the user needs to make deliberately.
- This project is not financial advice and doesn't guarantee profit or
  protect against liquidation if leverage is used. The backtester also
  assumes limit orders always fill at the intended price (no slippage
  modeling) — real performance will likely be somewhat worse than
  backtested performance.
- API keys are read from environment variables (`SHARK_API_KEY`,
  `SHARK_API_SECRET`) via `shark/config.py`, never stored in
  `config.json` or hardcoded. Keep it that way — config.json is meant to
  be freely editable/shareable, credentials are not.
- `.env` (gitignored, `python-dotenv` loads it automatically) is the
  other supported way to set those two vars, for local dev convenience.
  `.env.example` is the committed template — keep it in sync if the set
  of required env vars ever changes, and never put a real key in it.
- The original three standalone scripts (`shark_backtest.py`,
  `shark_mean_reversion_bot.py`, `shark_sma_bot.py`) have been removed -
  fully superseded by the `shark/` package. Apply fixes there, not by
  resurrecting a standalone script.

## New strategy: `psar_trend` (2026-09-16)

Added a fourth strategy, `shark/strategies/psar_trend.py` - Parabolic
SAR trend-following, long+short. Translated from a stop-and-reverse-style
options positional system the user's firm runs on Indian index options
(NIFTY/SENSEX/etc., a `ParabolicSAR` class + signal-inversion entry/exit,
random-UID large-scale parameter search). Only the PSAR signal itself and
the general "trend flip -> directional entry" shape translate to a
spot/futures context - everything options-specific (strike/moneyness
selection, expiry rollover, hedge legs, margin-based sizing formulas)
has no equivalent for a perpetual future and was left out entirely.

**Design**: flips into LONG when SAR crosses below price (uptrend
detected) and SHORT when SAR crosses above (downtrend), using the
strategy's own `_ParabolicSAR` class (standard Wilder-style, ported
fresh - not a copy of the firm's code, same well-known public algorithm).
Unlike `mean_reversion`/`scalp`, exits are primarily via the SAR level
itself acting as a trailing stop (re-read fresh from `indicators["sar"]`
every candle in `exit_decision`, not a static snapshot) - that's PSAR's
whole design point, distinct from every other strategy here which uses a
fixed SL/TP. A generous `take_profit_pct` exists only as a backstop cap.
`fee_gate_enabled = False`, matching `sma.py`'s precedent for
trend-following strategies (the fee gate exists for small-edge
fade/scalp entries, not directional trend capture).

**Real bug found and fixed during backtesting (2026-09-16)**: the first
version set `EntryPlan.stop_loss_price` to the live SAR value *at entry
time*, since that's the "true" stop for a trailing system. But
`shark/risk.py`'s `calc_position_size()` uses
`stop_distance = abs(entry_price - stop_loss_price)` as the position
sizing denominator - and SAR can sit very close to price right after a
fresh flip (it jumps to the prior swing extreme, which isn't always far
away). A tiny stop distance produced enormous position sizes, totally
decoupled from the actual exit (which uses a different, moving SAR value
each candle, not the entry-time snapshot). This inflated the backtest to
+261% net / PF 6.68, then a walk-forward version of the same bug produced
+400%/PF 375/0.37% drawdown - obviously not real trading numbers, caught
before ever being taken seriously. **Fix**: `EntryPlan.stop_loss_price`
is now a **fixed `stop_loss_pct`** distance from entry (same convention
as every other strategy here), used for sizing AND as a hard backstop
checked first in `exit_decision` - the SAR trail remains the primary,
usually-tighter exit. `config.json`'s `strategies.psar_trend` gained a
`stop_loss_pct` field (`2.0`) alongside `initial_af`/`max_af`/
`entry_limit_offset_pct`/`take_profit_pct`. **Lesson**: for any strategy
with a dynamic/trailing exit, the position-sizing stop distance must
stay a stable value fixed at entry - never wire the same live-recomputed
indicator into both the sizing denominator and the actual exit trigger,
or a small sizing denominator can silently blow up position size with no
guardrail.

### Walk-forward validation methodology adopted (2026-09-16)

Up to this point, this project's tuning process swept parameters and
validated on the *same* historical window - the exact pattern that
produced two false "validated" results before (BTCUSDT's 83%->66% win
rate collapse, XAUINR's short-sweep mirage, both documented above). To
stop repeating that mistake, adopted a proper walk-forward split for
comparing strategies: sweep parameters on the first 70% of a 4500-candle
XAUINR history (train), then evaluate the winning config *only* on the
untouched last 30% (test) - a number the tuning process never saw and
couldn't have shaped itself around. Scripts are one-off
(`walkforward_xauinr.py` for `mean_reversion`, `walkforward_psar.py` for
`psar_trend`), not part of the committed codebase - re-run manually if
this needs repeating with fresh data.

**Result: exposed that `mean_reversion`'s edge is much thinner than it
looked.** Most of 3,778 viable train-set configs collapsed or barely
survived on the test set (several went from +5%+ on train to near-zero
or negative on test) - the same overfitting pattern as before, just
smaller-magnitude this time. The best config that actually held up:
`bb_period=14 bb_stddev=2.5 rsi_period=14 [35,65] atr_thresh=0.8 sl=1.5
tp=1.5 mptfr=2.0` - TRAIN net=4.10%/win=69.4%/PF=1.38, but only
**TEST net=2.37%/win=62.5%/16 trades/PF=1.67** over the ~56-day test
window. That's the honest number - `config.json` has NOT been updated to
this config since it isn't clearly better than the existing
`bb_stddev=2.0 atr=0.5 sl=1.5 tp=0.8` values (fewer trades doesn't
obviously beat more trades with similar-ish thin edge) - revisit if
`mean_reversion` is chosen over `psar_trend` for live use.

**`psar_trend`'s walk-forward held up much better**: win rate stayed
~87-90% consistently across both train and test at
`interval=1h af=0.03/0.3 offset=0.01 sl=2.0 tp=6-8` (corrected 2026-09-16 -
this was previously mistyped here as `max_af=0.2`; `config.json` was
also wrong, running `15m`/`af=0.02`/`tp=4.0` the whole time, meaning
`psar_trend` had never actually run its own validated config until now -
fixed directly on this branch ahead of the multi-day dry-run test),
with **TEST net=+24.03%, PF=16.96, maxDD=1.03%, 78 trades** over the same
~56-day test window. This is a real robustness signal (train and test
performance are similar, not a collapse) - but comes with a specific,
unresolved caveat: XAUINR (gold) has been in a sustained uptrend through
this entire dataset, and PSAR is a pure trend-rider. This result may
reflect "gold went up and staying long was correct" more than a
repeatable edge across regimes - **it has never been tested against a
ranging or downtrending gold market**. Treat as the stronger candidate
on current evidence, not a settled choice.

**Decision as of 2026-09-16: `psar_trend` is the leading candidate**,
chosen over `mean_reversion` based on the walk-forward comparison above.
Not yet dry-run tested for an extended live stretch - that's the
recommended next step before considering it for real capital, same as
every strategy here.

**Follow-up check: does a shorter interval rescue `mean_reversion`?
(2026-09-16, same day)** Before settling on `psar_trend`, re-ran the same
walk-forward methodology for `mean_reversion` at `kline_interval="15m"`
(same 7-dim grid, same XAUINR data source, 3150 train / 1350 test
candles) to see whether the 1h result's thin edge was an interval
artifact rather than a real ceiling. It wasn't - 15m came back *weaker*,
not stronger: best out-of-sample candidate
(`bb=14/2.5 rsi=14 [35,65] atr=0.3 sl=1.0 tp=0.8`) only reached
**TEST net=0.27%, win=58.3%, 12 trades, PF=1.19, maxDD=1.34%** -
effectively breakeven, and most of the other top-train configs collapsed
to negative test returns (same overfitting pattern seen before, just
smaller-magnitude). Three-way out-of-sample comparison:

| strategy         | interval | test net% | test win% | test trades | test PF | test maxDD |
|------------------|----------|-----------|-----------|--------------|---------|-------------|
| mean_reversion   | 1h       | +2.37%    | 62.5%     | 16           | 1.67    | 1.31%       |
| mean_reversion   | 15m      | +0.27%    | 58.3%     | 12           | 1.19    | 1.34%       |
| **psar_trend**   | 1h       | **+24.03%** | **87.2%** | **78**     | **16.96** | **1.03%** |

`psar_trend` wins decisively on every metric, not just net return - this
closes out the "can mean_reversion be tuned further" question raised
this session; a shorter interval was the most promising untried lever
and it didn't move the needle. `psar_trend` remains the leading
candidate, with the regime-dependency caveat above still unresolved.

### Strategies evaluated and rejected from the user's firm's codebase (2026-09-16)

The user shared four strategies from their trading firm's Indian
index-options system for evaluation as crypto-translation candidates.
Only PSAR (above) was built. For future reference, the other three were
rejected and why:

- **BTST/ORB-style breakout** (`BTSTDIRV2`): opening-range-breakout entry
  + a ratcheting trailing stop/target (both climb together as price
  moves favorably). The ORB entry and session/DTE plumbing don't
  translate (crypto has no session open/close or options expiry), but
  the **ratcheting trail is a genuinely more sophisticated exit mechanism
  than anything in this codebase**, including `psar_trend`'s plain SAR
  trail - worth revisiting as an upgrade to `psar_trend`'s exit logic
  later, not as a new standalone strategy.
- **DMA crossover** (`DMA`): single daily moving average vs. daily close,
  long ATM puts when below the MA. Rejected because it's strictly
  *simpler* than the existing `sma.py` (single MA vs. fast/slow
  crossover) - would add a weaker version of a strategy already here.
- **Hedged short strangle** (`MINPMISL`): sells CE+PE with OTM hedge legs
  for defined-risk margin, SPAN-style margin sizing. Rejected outright -
  Shark Exchange has no options market, so there's no instrument to
  construct this trade with at all; every load-bearing piece is
  options/margin-specific.
