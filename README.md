# shark-trader

Algo trading bot for Shark Exchange (sharkexchange.in).

One entry point (`main.py`), one config file (`config.json`), two
selectable strategies. See `CLAUDE.md` for the design rationale behind
this structure.

## Setup

```
python -m venv venv
venv\Scripts\activate          # Windows; source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

For live/dry-run trading (not needed for backtesting, since klines is a
public endpoint), set your API credentials. Either export them:
```
export SHARK_API_KEY=your_key
export SHARK_API_SECRET=your_secret
```
(PowerShell: `$env:SHARK_API_KEY = "your_key"`)

...or copy `.env.example` to `.env` and fill in the values there —
`main.py` loads it automatically on startup (via `python-dotenv`).
`.env` is gitignored; never commit real keys.

## 1. Backtest first — validate before running anything live

```
python main.py backtest --strategy mean_reversion
python main.py backtest --strategy sma
```

Replays the chosen strategy against historical candles from Shark
Exchange's public klines endpoint and reports win rate, net P&L after
fees, profit factor, and max drawdown, plus an equity curve PNG
(`backtest_equity_curve.png`, path configurable in `config.json`).

Look specifically at **net P&L after fees**, not gross — that's the
number that tells you whether the edge survives real trading costs. If
it doesn't, tune the strategy's config block (wider `take_profit_pct`,
stricter entry filter, different `kline_interval`) and re-run before
going anywhere near real money.

## 2. Run live/dry-run

```
python main.py run --strategy mean_reversion
python main.py run --strategy sma
```

`"dry_run": true` in `config.json` by default — leave it there until
you've watched a session's summary and trust the logic. Flip to `false`
only when ready to risk real money, and start with a small
`account_capital`.

Every closed trade is logged to `shark_bot_trade_log_<strategy>.csv`,
with a running summary printed after each trade and a final summary on
shutdown (Ctrl+C or exit) — so a dry run produces the same kind of
report as the backtest, not just a scrolling log.

## The three strategies

- **`mean_reversion`** (the validated default): Bollinger Bands + RSI
  entries, ATR trend filter to avoid trading strong trends. Fee-aware —
  limit (maker) orders for entries/take-profit, market (taker) only for
  urgent stop-loss exits, and skips trades whose edge is too thin to
  clear round-trip fees (`min_profit_to_fee_ratio` in config). Long-only.
- **`sma`** (trend-following, kept for reference): fast/slow SMA
  crossover, plain market orders, no fee gate. Different risk/reward
  shape than mean reversion (fewer, bigger wins vs. many small wins) —
  backtest it yourself before trusting it; it has not been tuned to the
  same degree as mean_reversion. Long-only.
- **`scalp`** (frequent, quick trades, **untuned**): fast/tight BB+RSI on
  1m candles, going both long (buy near lower band) and short (sell near
  upper band), with a max-hold-time forced exit so positions don't sit
  open indefinitely. Backtest first — the starting config values came
  back net-negative on the first run, so treat this as a starting point
  to tune, not a working default. See CLAUDE.md for the long/short
  interface changes this required.

## Editing settings

Everything tunable lives in `config.json` — symbol, risk %, fees,
poll interval, and each strategy's own parameters under
`strategies.<name>`. No code changes needed for routine tuning.
API keys are the one exception: they never go in `config.json`. Save them
on the dashboard's Settings page (written to the gitignored
`credentials.json`), or use the `SHARK_API_KEY` / `SHARK_API_SECRET`
environment variables as a fallback.

## Adding a new strategy

Implement the `Strategy` interface in `shark/strategies/base.py`
(entry/exit signal logic only — position sizing, order placement, fees,
and reporting are handled for you by the shared engines), add a config
block under `strategies` in `config.json`, and register the class in
`shark/strategies/__init__.py`'s `STRATEGIES` dict.

## Before running anything live

- Re-verify current fees at `sharkexchange.in/fee-structure` — `config.json`
  has 0.016% maker / 0.040% taker + 18% GST as of when this was built;
  update `fees.maker_fee_pct` / `fees.taker_fee_pct` if Shark Exchange
  changes them.
- The `/v1/order/place-order` request schema is doc-verified (see
  CLAUDE.md) but not live-tested — the first real order is still a
  small risk of a 400 on an unexpected field. Start tiny.
- None of this is financial advice, and no backtest or risk-management
  layer guarantees profit or eliminates liquidation risk if you use
  leverage.
