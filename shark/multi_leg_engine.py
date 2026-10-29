"""Dry-run loop for multi-leg strategies (macd_multi). Never places an order: entries and exits are simulated
on CLOSED candles, with the same fee, slippage and INR-rate maths the single-position engine books in dry run.
Only candles that close after the bot starts are acted on."""

import json
import os
import time

import requests

from .orders import effective_fee_pct
from .strategies.macd_multi import MultiEntryBook


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
        sgn = 1 if leg["side"] == "LONG" else -1
        gross = sgn * (px - leg["entry"]) * leg["qty"] * quote_to_inr_rate
        fee = (leg["entry"] * taker + px * (maker if is_maker else taker)) / 100 * leg["qty"] * quote_to_inr_rate
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
                for leg, reason, px, is_maker in book.exits(c):
                    book_exit(leg, reason, px, is_maker)
                now, prev = series[i], series[i - 1]
                if now is None or prev is None:
                    continue
                side = book.signal(prev[0], prev[1], now[0], now[1], c["close"], now[2])
                if side is None:
                    continue
                leg, why = book.plan_entry(side, c["close"], now[2])
                if leg is None:
                    print(f"    {side} signal @ {c['close']:.2f} skipped: {why}")
                    continue
                if not risk.can_open_new_trade():
                    print(f"    {side} signal @ {c['close']:.2f} skipped: risk limits block new trades")
                    continue
                qty, info = risk.position_size(leg["entry"], leg["stop"])
                if qty <= 0:
                    print(f"    {side} signal @ {c['close']:.2f} skipped: size 0 ({info.get('reason', 'unknown')})")
                    continue
                leg["qty"] = qty
                book.add(leg)
                print(f">>> [SIM] Entry {leg['n']} {side} {qty} {symbol} @ {leg['entry']:.2f} | "
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
