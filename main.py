"""Shark Exchange trading bot - single entry point for every strategy.

Usage:
  python main.py backtest --strategy mean_reversion
  python main.py backtest --strategy sma
  python main.py run --strategy mean_reversion
  python main.py run --strategy sma

All tunable settings live in config.json - no code edits needed to
change symbol, risk %, or strategy parameters. See README.md.
"""

import argparse
import atexit
import signal
import sys

from shark.config import load_config, strategy_params
from shark.exchange import ExchangeClient
from shark.orders import OrderExecutor
from shark.performance import PerformanceTracker
from shark.risk import RiskManager
from shark.strategies import STRATEGIES
from shark import backtest_engine, live_engine


def build_strategy(name: str, config: dict):
    if name not in STRATEGIES:
        raise SystemExit(f"Unknown strategy '{name}'. Choices: {', '.join(STRATEGIES)}")
    cls = STRATEGIES[name]
    return cls(strategy_params(config, name))


def cmd_backtest(args, config: dict):
    strategy = build_strategy(args.strategy, config)
    exchange = ExchangeClient(config["base_url"])

    interval = strategy.params["kline_interval"]
    lookback = config["backtest"]["lookback_candles"]
    print(f"Fetching {lookback} historical candles for {config['symbol']} ({interval})...")
    candles = exchange.get_klines(config["symbol"], interval, lookback)
    print(f"Received {len(candles)} candles.")

    if len(candles) < strategy.min_candles_needed() + 10:
        print("Not enough candles returned to run a meaningful backtest. "
              "Try a smaller lookback_candles or check the API response.")
        return

    trades, equity_curve = backtest_engine.run_backtest(strategy, candles, config)
    backtest_engine.print_report(strategy.name, config["symbol"], interval, trades, equity_curve, config)
    backtest_engine.plot_equity_curve(strategy.name, config["symbol"], interval, equity_curve, config)


def cmd_run(args, config: dict):
    strategy = build_strategy(args.strategy, config)
    exchange = ExchangeClient(config["base_url"], config["api_key"], config["api_secret"])
    executor = OrderExecutor(exchange, config["symbol"], config["dry_run"])

    info = exchange.get_exchange_info(config["symbol"])
    print(f"Exchange limits for {config['symbol']}: min_qty={info['min_qty']} "
          f"min_notional={info['min_notional']} {info['quote_asset']} "
          f"quantity_precision={info['quantity_precision']} max_leverage={info['max_leverage']}")

    # account_capital is always tracked in INR (project convention). If the
    # traded symbol is quoted in something else (e.g. BTCUSDT -> USDT),
    # every price/notional/min-notional number from the exchange is in
    # THAT currency, not INR - position sizing would be wrong by whatever
    # the INR/quote exchange rate is if we didn't convert. Derive that
    # rate from the exchange's own live prices: find an INR-quoted pair
    # for the same base asset (e.g. BTCINR for BTCUSDT) and compare.
    if info["quote_asset"] == "INR":
        quote_to_inr_rate = 1.0
    else:
        inr_symbol = f"{info['base_asset']}INR"
        try:
            inr_price = exchange.get_last_price(inr_symbol)
            quote_price = exchange.get_last_price(config["symbol"])
            quote_to_inr_rate = inr_price / quote_price
        except Exception as e:
            raise SystemExit(
                f"account_capital is tracked in INR but {config['symbol']} is quoted in "
                f"{info['quote_asset']} - position sizing needs an INR/{info['quote_asset']} "
                f"conversion rate. Tried deriving it from {inr_symbol} vs {config['symbol']} "
                f"live prices and failed: {e}. Refusing to run live with unverified position "
                f"sizing - trade an INR-quoted pair instead, or fix this rate derivation."
            )
        print(f"Derived INR/{info['quote_asset']} rate: {quote_to_inr_rate:.4f} "
              f"(from {inr_symbol} vs {config['symbol']} live prices)")

    lev_cfg = config["leverage"]
    if lev_cfg["value"] > info["max_leverage"]:
        raise SystemExit(f"config.json leverage.value ({lev_cfg['value']}) exceeds "
                          f"{config['symbol']}'s max_leverage ({info['max_leverage']}).")

    if config["dry_run"]:
        print(f"[DRY RUN] Would set leverage={lev_cfg['value']}x marginMode={lev_cfg['margin_mode']} "
              f"for {config['symbol']}")
    else:
        exchange.set_leverage(config["symbol"], lev_cfg["value"], lev_cfg["margin_mode"])
        print(f"Set leverage={lev_cfg['value']}x marginMode={lev_cfg['margin_mode']} for {config['symbol']}")

    risk = RiskManager(
        account_capital=config["account_capital"],
        risk_per_trade_pct=config["risk_per_trade_pct"],
        max_daily_loss_pct=config["max_daily_loss_pct"],
        max_consecutive_losses=config["max_consecutive_losses"],
        cooldown_minutes_after_halt=config["cooldown_minutes_after_halt"],
        leverage=lev_cfg["value"],
        min_notional=info["min_notional"],
        min_qty=info["min_qty"],
        quantity_precision=info["quantity_precision"],
        allow_min_notional_override=lev_cfg["allow_min_notional_override"],
        quote_to_inr_rate=quote_to_inr_rate,
    )
    tracker = PerformanceTracker(
        csv_path=f"shark_bot_trade_log_{strategy.name}.csv",
        starting_capital=config["account_capital"],
        symbol=config["symbol"],
    )

    atexit.register(tracker.print_final_summary, config["dry_run"])
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    live_engine.run(strategy, exchange, executor, risk, tracker, config)


def main():
    parser = argparse.ArgumentParser(description="Shark Exchange trading bot")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backtest = sub.add_parser("backtest", help="Backtest a strategy against historical candles")
    p_backtest.add_argument("--strategy", default="mean_reversion", choices=STRATEGIES.keys())

    p_run = sub.add_parser("run", help="Run a strategy live/dry-run against the poll loop")
    p_run.add_argument("--strategy", default="mean_reversion", choices=STRATEGIES.keys())

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "backtest":
        cmd_backtest(args, config)
    elif args.command == "run":
        cmd_run(args, config)


if __name__ == "__main__":
    main()
