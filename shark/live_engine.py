"""Strategy-agnostic live/dry-run poll loop."""

import time

import requests

from .exchange import ExchangeClient
from .orders import OrderExecutor, effective_fee_pct, fee_gate_passes, round_trip_fee_estimate
from .performance import PerformanceTracker
from .risk import RiskManager
from .strategies.base import Position, Strategy


def run(strategy: Strategy, exchange: ExchangeClient, executor: OrderExecutor,
        risk: RiskManager, tracker: PerformanceTracker, config: dict):
    symbol = config["symbol"]
    interval = strategy.params["kline_interval"]
    poll_seconds = config["poll_seconds"]
    dry_run = config["dry_run"]
    fees_cfg = config["fees"]
    maker_fee_pct = effective_fee_pct(fees_cfg["maker_fee_pct"], fees_cfg["gst_pct"])
    taker_fee_pct = effective_fee_pct(fees_cfg["taker_fee_pct"], fees_cfg["gst_pct"])
    min_profit_to_fee_ratio = strategy.params.get("min_profit_to_fee_ratio", 0)

    print(f"Starting {strategy.name} bot on {symbol} ({interval}) | dry_run={dry_run}")
    print(f"Capital: {config['account_capital']} INR | Risk/trade: {config['risk_per_trade_pct']}%")
    print(f"Maker fee (eff.): {maker_fee_pct:.4f}% | Taker fee (eff.): {taker_fee_pct:.4f}%")

    position: Position | None = None
    bars_since_open = 0
    limit = max(strategy.min_candles_needed() + 10, 30)

    while True:
        try:
            candles = exchange.get_klines(symbol, interval, limit=limit)
            if not candles:
                print("No candle data returned, waiting...")
                time.sleep(poll_seconds)
                continue

            price = candles[-1]["close"]
            indicators = strategy.compute_indicators(candles)
            print(f"{strategy.status_line(price, indicators)} "
                  f"position={position.side if position else 'None'}")

            if position is not None:
                decision = strategy.exit_decision(position, candles[-1], indicators, bars_since_open)
                if decision is not None:
                    reason, exit_price, exit_is_maker = decision
                    close_side = "SELL" if position.side == "LONG" else "BUY"
                    if exit_is_maker:
                        print(f">>> Exit ({reason}) at {exit_price:.2f} via LIMIT (maker)")
                        executor.place_limit_order(close_side, position.qty, exit_price, reduce_only=True)
                    else:
                        print(f">>> Exit ({reason}) at {exit_price:.2f} via MARKET (taker)")
                        executor.place_market_order(close_side, position.qty, reduce_only=True)

                    if position.side == "LONG":
                        gross_pnl = (exit_price - position.entry_price) * position.qty
                    else:
                        gross_pnl = (position.entry_price - exit_price) * position.qty
                    fee_pct = round_trip_fee_estimate(maker_fee_pct, taker_fee_pct,
                                                       position.entry_is_maker, exit_is_maker)
                    fees = position.entry_notional * fee_pct / 100
                    risk.record_trade_result(gross_pnl, fees)
                    tracker.record(position.entry_price, exit_price, position.qty, reason, gross_pnl, fees)
                    position = None
                    bars_since_open = 0
            else:
                plan = strategy.entry_plan(price, candles[-1], indicators)
                if plan is not None:
                    if not risk.can_open_new_trade():
                        print("Signal found, but risk limits block new trades right now.")
                    elif strategy.fee_gate_enabled and not fee_gate_passes(
                            plan, maker_fee_pct, taker_fee_pct, min_profit_to_fee_ratio):
                        print(f"Signal found, but TP doesn't clear {min_profit_to_fee_ratio}x "
                              f"round-trip fees - skipping trade.")
                    else:
                        qty, size_info = risk.position_size(plan.entry_price, plan.stop_loss_price)
                        if qty <= 0:
                            reason = size_info.get("reason", "unknown")
                            if reason == "below_min_notional_override_disabled":
                                print(f"Signal found, but the risk-sized position (~{size_info['ideal_notional_inr']:.2f} "
                                      f"INR notional) is below the exchange's min notional "
                                      f"(~{size_info['min_notional_inr']:.2f} INR) - skipping rather than take on more "
                                      f"risk than configured. Set allow_min_notional_override to trade this "
                                      f"anyway (accepts higher real risk per trade - see CLAUDE.md).")
                            elif reason == "insufficient_margin":
                                print(f"Signal found, but required margin (~{size_info['margin_required_inr']:.2f} INR "
                                      f"at {size_info['leverage']}x leverage) exceeds account capital "
                                      f"({size_info['account_capital']:.2f} INR) - skipping trade.")
                            else:
                                print(f"Calculated position size is 0 ({reason}) - skipping trade.")
                        else:
                            if size_info.get("forced_by_min_notional"):
                                print(f"NOTE: position sized up to clear min notional - real risk this trade "
                                      f"is {size_info['risk_effective_pct']:.2f}% of capital, not the "
                                      f"configured {size_info['risk_intended_pct']:.2f}%.")
                            open_side = "BUY" if plan.side == "LONG" else "SELL"
                            side_desc = "LIMIT (maker)" if plan.is_maker else "MARKET (taker)"
                            print(f">>> Entry signal ({plan.side}) -> {open_side} {qty:.6f} {symbol} "
                                  f"via {side_desc} @ {plan.entry_price:.2f}")
                            if plan.is_maker:
                                executor.place_limit_order(open_side, qty, plan.entry_price, reduce_only=False)
                            else:
                                executor.place_market_order(open_side, qty, reduce_only=False)
                            position = Position(
                                entry_price=plan.entry_price, qty=qty,
                                entry_notional=plan.entry_price * qty,
                                stop_loss_price=plan.stop_loss_price,
                                take_profit_price=plan.take_profit_price,
                                entry_is_maker=plan.is_maker,
                                side=plan.side,
                                strategy_tag=plan.strategy_tag,
                            )
                            bars_since_open = 0
                            print(f"    entry={position.entry_price:.2f} SL={position.stop_loss_price:.2f} "
                                  f"TP={position.take_profit_price:.2f}")

            strategy.note_indicators(indicators)

        except requests.exceptions.HTTPError as err:
            body = err.response.text if err.response is not None else str(err)
            print(f"HTTP error: {body}")
        except Exception as e:
            print(f"Unexpected error: {e}")

        if position is not None:
            bars_since_open += 1

        time.sleep(poll_seconds)
