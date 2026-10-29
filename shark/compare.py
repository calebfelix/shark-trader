"""Compare every strategy on the same symbol with the realistic fill model and an out-of-sample split.

For each strategy (at its own configured interval) the candles are split chronologically: the first 70%
is "train", the last 30% "test". Nothing is tuned here - the point is whether the config as it stands holds
up on data it was not picked on, and how much worse realistic fills are than idealised ones.
"""

import copy

from . import backtest_engine
from .strategies import STRATEGIES

INTERVAL_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
MIN_TRADES_FOR_CONFIDENCE = 30


def summarize(trades, curve, start_capital: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in trades if t.net_pnl <= 0]
    gross = sum(t.gross_pnl for t in trades)
    fees = sum(t.fees for t in trades)
    net = sum(t.net_pnl for t in trades)
    return {
        "trades": n, "win_rate": len(wins) / n * 100, "net_pct": net / start_capital * 100,
        "gross_pct": gross / start_capital * 100, "fees_pct": fees / start_capital * 100,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else float("inf"),
        "max_dd": backtest_engine.max_drawdown(curve),
    }


def run_one(name: str, config: dict, candles: list[dict], realistic: bool) -> dict:
    cfg = copy.deepcopy(config)
    cfg["backtest"] = dict(cfg.get("backtest", {}), realistic_fills=realistic)
    params = dict(cfg["strategies"][name])
    strategy = STRATEGIES[name](params)
    trades, curve = backtest_engine.run_backtest(strategy, candles, cfg)
    return summarize(trades, curve, cfg["account_capital"])


def split(candles: list[dict], train_frac: float = 0.7) -> tuple[list[dict], list[dict]]:
    cut = int(len(candles) * train_frac)
    return candles[:cut], candles[cut:]


def fmt(s: dict) -> str:
    if not s.get("trades"):
        return "no trades"
    pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    flag = "" if s["trades"] >= MIN_TRADES_FOR_CONFIDENCE else "  (few trades)"
    return (f"{s['trades']:>4} trades  win {s['win_rate']:5.1f}%  net {s['net_pct']:+8.1f}%  "
            f"gross {s['gross_pct']:+8.1f}%  fees {s['fees_pct']:5.1f}%  PF {pf:>5}  maxDD {s['max_dd']:5.1f}%{flag}")


def run_compare(config: dict, exchange, names: list[str], days: int, max_candles: int) -> None:
    symbol = config["symbol"]
    print(f"Comparing {', '.join(names)} on {symbol} - realistic fills "
          f"(taker entries/exits, {config['backtest'].get('slippage_pct', 0.02)}% slippage, gaps fill at the open)\n")
    for name in names:
        if getattr(STRATEGIES[name], "multi_leg", False):
            print(f"=== {name}: skipped (several entries per setup - not supported by this backtester)\n")
            continue
        interval = config["strategies"][name]["kline_interval"]
        per_day = 1440 / INTERVAL_MINUTES[interval]
        want = int(days * per_day)
        n = min(want, max_candles)
        candles = exchange.get_klines(symbol, interval, n)
        span = len(candles) / per_day
        note = f"  [capped: {span:.0f} of {days} days]" if want > max_candles else ""
        print(f"=== {name}  ({interval}, {len(candles)} candles = {span:.0f} days){note}")
        if len(candles) < 200:
            print("    not enough candles\n")
            continue
        train, test = split(candles)
        print(f"  idealised fills (the old backtest) : {fmt(run_one(name, config, candles, False))}")
        print(f"  realistic - whole period           : {fmt(run_one(name, config, candles, True))}")
        print(f"  realistic - first 70% (train)      : {fmt(run_one(name, config, train, True))}")
        print(f"  realistic - last 30%  (TEST)       : {fmt(run_one(name, config, test, True))}\n")
