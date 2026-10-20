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

## 3. Dashboard

```
python dashboard.py            # then open http://127.0.0.1:8765
```

A local web page (FastAPI) showing the bot's closed trades, calculated
equity and performance stats, the open position, the bot's console output,
and Start/Stop buttons. The **Settings** page saves your API key and secret
to `credentials.json` (gitignored, never pushed) and switches between
**dry run** (simulated, the default) and **real account** (needs saved keys
and typing `REAL MONEY`). In real-account mode it also shows your actual
Shark wallet balance (read-only).

Things to know:
- "Bot equity (calculated)" is the bot's own estimate from `config.json`'s
  `account_capital` plus its trade log. In real-account mode the separate
  "Exchange wallet" card is the real balance.
- It listens on `127.0.0.1` only and has **no login**. Don't expose it to the
  internet; on a server, reach it through an SSH tunnel (below).
- Mode and credentials live in `local_settings.json` / `credentials.json`,
  both gitignored. `config.json` stays `dry_run: true` in git.

## 4. Running on a VPS (Linux)

Steps for an Ubuntu/Debian server. The code is cross-platform (process
handling uses `psutil`, verified on Windows), but these steps haven't been run
on a real VPS yet - if a step fails, fix the doc. The bot and dashboard run on
the VPS; you open the dashboard from your own machine over SSH.

**1. Get the code and install**
```
sudo apt update && sudo apt install -y python3 python3-venv git
git clone https://github.com/calebfelix/shark-trader.git
cd shark-trader
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**2. Keep the clock accurate** — signed requests fail with `error 4008
"Timestamp older than 5 seconds"` if the server clock drifts:
```
sudo timedatectl set-ntp true
timedatectl            # "System clock synchronized: yes"
```

**3. Edit `config.json` on the server** — `symbol`, `account_capital`,
`risk_per_trade_pct`, `leverage`, etc. Keep `"dry_run": true` to start. (If
you later `git pull` and hit a conflict on `config.json`, `git stash`
your edit, pull, then `git stash pop`.)

**4. Whitelist the server's IP on your Shark API key.** Find it with
`curl -4 ifconfig.me`, then add that address to the key's allowed IPs in
your Shark Exchange API settings. Without it, signed calls fail with
`error 4009 "IP address not whitelisted"`. Use a VPS with a fixed IP.

**5. Run the dashboard as a service**
```
# edit User and paths in the file first (defaults assume user "trader" in /home/trader)
sudo cp deploy/shark-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shark-dashboard
sudo systemctl status shark-dashboard
```

**6. Open the dashboard from your own computer** with an SSH tunnel:
```
ssh -L 8765:127.0.0.1:8765 trader@YOUR_VPS_IP
```
then browse to http://127.0.0.1:8765 on your machine. Keep the SSH session
open while you use it. Don't open port 8765 in the firewall.

**7. Add keys and start.** On the Settings page paste your API key and
secret (saved on the server in `credentials.json`, mode 600). Pick a
strategy on the main page and press **Start**. Watch a dry run first;
switch to real account only when you're ready, from the same Settings page.

**Optional: auto-restart the bot.** `deploy/shark-bot.service` runs the bot
itself as a service that restarts after crashes/reboots (useful if the
exchange is down for maintenance when it starts). Use it *instead of*
starting the bot from the dashboard, and stop it with
`sudo systemctl stop shark-bot` rather than the dashboard's Stop button.

**Updating the server**
```
cd shark-trader && git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart shark-dashboard      # a running bot keeps running
```

**Backups / state.** Everything the server owns is gitignored and local:
`credentials.json`, `local_settings.json`, `shark_bot_trade_log_*.csv`,
`dashboard_bot.log`. Back up the CSVs if you want the trade history.

**Troubleshooting**

| Symptom | Cause / fix |
|---|---|
| `4009 IP address not whitelisted` | Add the VPS IP (not your home IP) to the API key, and save/confirm it. |
| `4008 Timestamp older than 5 seconds` | Clock drift - enable NTP (step 2). |
| `503 no healthy upstream` / startup crash | Exchange maintenance or outage; wait and restart (or use the bot service, which retries). |
| Dashboard unreachable | Check `systemctl status shark-dashboard` and that your SSH tunnel is open. |
| Bot "already running" but you can't see it | Another `main.py run` process exists (e.g. the bot service); `pgrep -af "main.py run"`. |

**Security checklist:** SSH key login (disable password auth), firewall allowing
only SSH, dashboard reachable only through the tunnel, API key created
with the minimum permissions and IP whitelist, and `dry_run` left on until
you've verified behavior with small amounts.

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
