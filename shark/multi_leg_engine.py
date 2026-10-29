"""Multi-leg strategies (macd_multi): a dry-run loop and a backtest that share one per-candle step(), so the
backtest runs exactly the rules the bot runs. Never places an order: entries and exits are simulated on CLOSED
candles, with the same fee, slippage and INR-rate maths the single-position engine books in dry run. The dry-run
loop only acts on candles that close after the bot starts."""

import json
import os
import time

import requests

from .backtest_engine import max_drawdown
from .orders import effective_fee_pct
from .risk import calc_position_size
from .strategies.macd_multi import MultiEntryBook


def leg_result(leg: dict, exit_px: float, is_maker: bool, maker_pct: float, taker_pct: float,
               rate: float = 1.0) -> tuple[float, float]:
    """(gross, fees) of a closed leg in INR: taker entry, maker target or taker stop."""
    sgn = 1 if leg["side"] == "LONG" else -1
    gross = sgn * (exit_px - leg["entry"]) * leg["qty"] * rate
    fees = (leg["entry"] * taker_pct + exit_px * (maker_pct if is_maker else taker_pct)) / 100 * leg["qty"] * rate
    return gross, fees


def step(book: MultiEntryBook, candle: dict, prev, now, size_fn, can_open) -> tuple[list, dict | None, str | None]:
    """One CLOSED candle: exits first (legs from earlier candles), then at most one new entry.
    prev/now are the strategy's (macd, signal, ema) for the previous and this candle (None while warming up).
    size_fn(entry, stop) -> (qty, info); can_open() -> bool. Returns (exits, new leg or None, skip note)."""
    exits = book.exits(candle)
    if now is None or prev is None:
        return exits, None, None
    side = book.signal(prev[0], prev[1], now[0], now[1], candle["close"], now[2])
    if side is None:
        return exits, None, None
    leg, why = book.plan_entry(side, candle["close"], now[2])
    if leg is None:
        return exits, None, f"{side} signal @ {candle['close']:.2f} skipped: {why}"
    if not can_open():
        return exits, None, f"{side} signal @ {candle['close']:.2f} skipped: risk limits block new trades"
    qty, info = size_fn(leg["entry"], leg["stop"])
    if qty <= 0:
        return exits, None, f"{side} signal @ {candle['close']:.2f} skipped: size 0 ({info.get('reason', 'unknown')})"
    leg["qty"] = qty
    book.add(leg)
    return exits, leg, None


def backtest(strategy, candles30: list[dict], candles4h: list[dict], config: dict) -> tuple[list[dict], list[float]]:
    """Every candle passed in must be CLOSED. Sizes each entry at risk_per_trade_pct of current equity (like
    backtest_engine). Open legs are closed at the last close at the end. Returns (closed legs, equity curve)."""
    fees = config["fees"]
    maker = effective_fee_pct(fees["maker_fee_pct"], fees["gst_pct"])
    taker = effective_fee_pct(fees["taker_fee_pct"], fees["gst_pct"])
    book = MultiEntryBook(strategy.params, config.get("backtest", {}).get("slippage_pct", 0.02))
    series = strategy.series(candles30, candles4h)
    equity, curve, done = float(config["account_capital"]), [float(config["account_capital"])], []

    def close(leg, reason, px, is_maker):
        nonlocal equity
        gross, fee = leg_result(leg, px, is_maker, maker, taker)
        equity += gross - fee
        curve.append(equity)
        done.append({**leg, "exit": px, "reason": reason, "gross": gross, "fees": fee, "net": gross - fee,
                     "r": (gross - fee) / (abs(leg["entry"] - leg["stop"]) * leg["qty"])})

    size = lambda e, s: (calc_position_size(e, s, config["risk_per_trade_pct"], equity), {})
    for i in range(1, len(candles30)):
        exits, _, _ = step(book, candles30[i], series[i - 1], series[i], size, lambda: True)
        for leg, reason, px, is_maker in exits:
            close(leg, reason, px, is_maker)
    for leg in list(book.legs):
        close(leg, "end_of_data", candles30[-1]["close"], False)
    return done, curve


def print_backtest(name: str, symbol: str, legs: list[dict], curve: list[float], capital: float) -> None:
    print("\n" + "=" * 60)
    print(f"BACKTEST REPORT - {name} - {symbol}")
    print("=" * 60)
    if not legs:
        print("No entries were triggered.")
        return
    setups: dict = {}
    for leg in legs:
        setups[leg["setup"]] = setups.get(leg["setup"], 0.0) + leg["net"]
    net = sum(x["net"] for x in legs)
    print(f"Entries: {len(legs)} in {len(setups)} setups | entries won: "
          f"{sum(x['net'] > 0 for x in legs) / len(legs) * 100:.1f}% | setups won: "
          f"{sum(v > 0 for v in setups.values()) / len(setups) * 100:.1f}%")
    print(f"Gross: {sum(x['gross'] for x in legs):+.2f} | Fees: {sum(x['fees'] for x in legs):.2f} | "
          f"Net: {net:+.2f} INR ({net / capital * 100:+.2f}%)")
    print(f"Average R per entry: {sum(x['r'] for x in legs) / len(legs):+.3f} | worst setup: "
          f"{min(setups.values()):+.2f} INR | max drawdown: {max_drawdown(curve):.2f}%")
    print(f"Final equity: {curve[-1]:.2f} INR (start {capital:.2f})")


def run(strategy, exchange, risk, tracker, config: dict, quote_to_inr_rate: float = 1.0):
    if config.get("dry_run") is not True:
        raise SystemExit(f"{strategy.name} is dry-run only - refusing to run in real-account mode.")
    p, symbol = strategy.params, config["symbol"]
    fees = config["fees"]
    maker = effective_fee_pct(fees["maker_fee_pct"], fees["gst_pct"])
    taker = effective_fee_pct(fees["taker_fee_pct"], fees["gst_pct"])
    book = MultiEntryBook(p, config.get("backtest", {}).get("slippage_pct", 0.02))
    state_path = f"dry_position_{symbol}.json"
    last_done = None          # open time of the last 30m candle already processed

    print(f"Starting {strategy.name} bot on {symbol} ({p['kline_interval']} entries, {p['trend_interval']} "
          f"EMA{p['ema_period']} trend) | dry_run=True | up to {p['max_entries']} entries per setup, "
          f"targets {p['tp_r']}R, setups need a stop >= {p['min_stop_pct']}% away")
    print(f"Capital: {config['account_capital']} INR | Risk/entry: {config['risk_per_trade_pct']}%")
    print(f"Maker fee (eff.): {maker:.4f}% | Taker fee (eff.): {taker:.4f}%")

    def publish():
        agg = book.aggregate()
        if agg is None:
            try:
                os.remove(state_path)
            except FileNotFoundError:
                pass
            return
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"side": agg["side"], "qty": agg["qty"], "entry_price": agg["entry"], "stop_loss": agg["stop"],
                       "take_profit": agg["tp"], "rate": quote_to_inr_rate, "entry_fee_pct": taker,
                       "exit_fee_pct": taker, "strategy": strategy.name, "legs": agg["legs"]}, f)
        os.replace(tmp, state_path)

    def book_exit(leg, reason, px, is_maker):
        gross, fee = leg_result(leg, px, is_maker, maker, taker, quote_to_inr_rate)
        print(f"    Entry {leg['n']} of setup {leg['setup']} closed ({reason}) @ {px:.2f}: "
              f"gross {gross:+.2f} | fees -{fee:.2f} | net {gross - fee:+.2f} INR")
        risk.record_trade_result(gross, fee)
        tracker.record(leg["entry"], px, leg["qty"], reason, gross, fee,
                       extra={"side": leg["side"], "source": "simulated", "entry_role": "taker",
                              "exit_role": "maker" if is_maker else "taker",
                              "position_id": f"setup{leg['setup']}-entry{leg['n']}"})

    publish()   # clears a stale file from an earlier run
    while True:
        try:
            c30 = exchange.get_klines(symbol, p["kline_interval"], p["history_30m"])
            c4 = exchange.get_klines(symbol, p["trend_interval"], p["history_4h"])
            closed30, closed4 = c30[:-1], c4[:-1]       # the last candle of each is still forming
            if not closed30:
                raise ValueError("no candle data")
            if last_done is None:
                last_done = closed30[-1]["time"]
                print(f"Watching from the next {p['kline_interval']} close (nothing in the past is traded).")
            series = strategy.series(closed30, closed4)
            for i in range(1, len(closed30)):
                c = closed30[i]
                if c["time"] <= last_done:
                    continue
                last_done = c["time"]
                exits, leg, note = step(book, c, series[i - 1], series[i], risk.position_size, risk.can_open_new_trade)
                for x in exits:
                    book_exit(*x)
                if note:
                    print(f"    {note}")
                if leg:
                    print(f">>> [SIM] Entry {leg['n']} {leg['side']} {leg['qty']} {symbol} @ {leg['entry']:.2f} | "
                          f"stop {leg['stop']:.2f} (shared) | target {leg['tp']:.2f}")
            publish()
            agg = book.aggregate()
            last = series[-1]
            ind = (f"EMA{p['ema_period']}({p['trend_interval']})={last[2]:.2f} MACD={last[0]:.4f}/{last[1]:.4f}"
                   if last else "warming up")
            print(f"price={c30[-1]['close']:.2f} {ind} entries={len(book.legs)} "
                  f"position={agg['side'] if agg else 'None'}")
        except requests.exceptions.HTTPError as err:
            body = err.response.text if err.response is not None else str(err)
            print(f"HTTP error: {body[:300]}")
        except Exception as e:
            print(f"Unexpected error: {e}")
        time.sleep(config["poll_seconds"])
