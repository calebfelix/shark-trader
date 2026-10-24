"""Strategy-agnostic live/dry-run poll loop."""

import json
import os
import time

import requests

from .exchange import ExchangeClient
from .intervals import INTERVAL_MS, bars_held
from .bot_registry import BotRegistry
from .live_sync import ExchangeSync, Pending
from .orders import OrderExecutor, effective_fee_pct, fee_gate_passes, round_trip_fee_estimate
from .performance import PerformanceTracker
from .risk import RiskManager
from .strategies.base import Position, Strategy


def run(strategy: Strategy, exchange: ExchangeClient, executor: OrderExecutor,
        risk: RiskManager, tracker: PerformanceTracker, config: dict,
        quote_to_inr_rate: float = 1.0):
    symbol = config["symbol"]
    interval = strategy.params["kline_interval"]
    interval_ms = INTERVAL_MS[interval]
    poll_seconds = config["poll_seconds"]
    dry_run = config["dry_run"]
    fees_cfg = config["fees"]
    maker_fee_pct = effective_fee_pct(fees_cfg["maker_fee_pct"], fees_cfg["gst_pct"])
    taker_fee_pct = effective_fee_pct(fees_cfg["taker_fee_pct"], fees_cfg["gst_pct"])
    min_profit_to_fee_ratio = strategy.params.get("min_profit_to_fee_ratio", 0)

    print(f"Starting {strategy.name} bot on {symbol} ({interval}) | dry_run={dry_run}")
    print(f"Capital: {config['account_capital']} INR | Risk/trade: {config['risk_per_trade_pct']}%")
    print(f"Maker fee (eff.): {maker_fee_pct:.4f}% | Taker fee (eff.): {taker_fee_pct:.4f}%")

    real = not dry_run
    registry = getattr(executor, "registry", None) or BotRegistry(None)
    executor.registry = registry
    sync = ExchangeSync(exchange, executor, symbol, strategy, registry) if real else None
    foreign_warned: set = set()
    order_timeout = config.get("order_timeout_seconds", 180)
    pending_entry: Pending | None = None
    pending_exit: Pending | None = None
    if real:
        print("Real-account mode: the exchange's open position is the source of truth "
              f"(reconciled every {poll_seconds}s); a hard stop-loss rests on the exchange.")

    dry_state_path = f"dry_position_{symbol}.json"

    def publish_dry(position):
        """Dry run: let the dashboard show the simulated open position (it has no exchange to ask)."""
        tmp = dry_state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"side": position.side, "qty": position.qty, "entry_price": position.entry_price,
                       "stop_loss": position.stop_loss_price, "take_profit": position.take_profit_price,
                       "opened_at_ms": position.opened_at_ms, "rate": quote_to_inr_rate,
                       "entry_fee_pct": maker_fee_pct if position.entry_is_maker else taker_fee_pct,
                       "exit_fee_pct": taker_fee_pct, "strategy": strategy.name}, f)
        os.replace(tmp, dry_state_path)

    def clear_dry():
        try:
            os.remove(dry_state_path)
        except FileNotFoundError:
            pass

    if dry_run:
        clear_dry()                                  # nothing is open at start

    def close_out(position, reason, exit_price, exit_is_maker):
        """Book a closed trade from ESTIMATED numbers (dry run, or when Shark's data is unavailable).
        Prices are in the quote currency (USDT...), but risk/tracker work in INR - convert."""
        if position.side == "LONG":
            gross_pnl = (exit_price - position.entry_price) * position.qty
        else:
            gross_pnl = (position.entry_price - exit_price) * position.qty
        fee_pct = round_trip_fee_estimate(maker_fee_pct, taker_fee_pct,
                                           position.entry_is_maker, exit_is_maker)
        fees = position.entry_notional * fee_pct / 100
        gross_pnl *= quote_to_inr_rate
        fees *= quote_to_inr_rate
        risk.record_trade_result(gross_pnl, fees)
        tracker.record(position.entry_price, exit_price, position.qty, reason, gross_pnl, fees,
                       extra={"side": position.side, "source": "simulated" if dry_run else "estimated",
                              "position_id": position.position_id or ""})

    def book_settled(position, reason, s):
        """Book a closed trade from Shark's own numbers (INR): nothing estimated."""
        costs = s["fees_inr"] - s["discounts_inr"] - s["funding_inr"]   # funding is signed: paid < 0
        print(f"    Settled from exchange: realized {s['realized_inr']:+.2f} | fees -{s['fees_inr']:.2f} "
              f"| fee discount +{s['discounts_inr']:.2f} | funding {s['funding_inr']:+.2f} "
              f"=> NET {s['net_inr']:+.2f} INR  (entry {s['entry_role']}, exit {s['exit_role']}, "
              f"exit @ {s['exit_price']:.2f})")
        risk.record_trade_result(s["realized_inr"], costs)
        tracker.record(s["entry_price"], s["exit_price"], s["qty"], reason, s["realized_inr"], s["fees_inr"],
                       discount=s["discounts_inr"], funding=s["funding_inr"],
                       extra={"side": position.side, "source": "settled", "entry_role": s["entry_role"],
                              "exit_role": s["exit_role"], "position_id": position.position_id or ""})

    position: Position | None = None
    limit = strategy.candles_to_fetch()
    last_entry_check = last_exit_check = None   # closed-candle strategies decide once per closed candle

    while True:
        try:
            candles = exchange.get_klines(symbol, interval, limit=limit)
            if not candles:
                print("No candle data returned, waiting...")
                time.sleep(poll_seconds)
                continue

            price = candles[-1]["close"]
            closed = candles[:-1]                      # the last candle is still forming
            view = closed if strategy.acts_on_closed_candles else candles
            indicators = strategy.compute_indicators(view)
            print(f"{strategy.status_line(price, indicators)} "
                  f"position={position.side if position else 'None'}")

            if real:
                raw_pos = sync.position()  # raises on API trouble -> skip this iteration
                ex_pos = raw_pos if (raw_pos is not None and sync.owns(raw_pos)) else None
                foreign = raw_pos if (raw_pos is not None and ex_pos is None) else None
                if foreign is not None and foreign["position_id"] not in foreign_warned:
                    foreign_warned.add(foreign["position_id"])
                    print(f"    NOTE: an open {symbol} position ({foreign['side']} {foreign['qty']} @ "
                          f"{foreign['entry_price']}) was NOT opened by this bot - hands off: not managing it, "
                          f"not placing a stop on it, and not opening new trades on {symbol} while it is open.")
                if position is None and ex_pos is not None:
                    plan = pending_entry.info.get("plan") if pending_entry else None
                    position = sync.adopt(ex_pos, plan)
                    pending_entry = None
                    note = {"plan": "", "saved": " (adopted: SL/TP restored from saved state)",
                            "recomputed": " (adopted: SL/TP recomputed from config - no saved plan)"}[sync.adopt_source]
                    print(f"    Confirmed on exchange: {position.side} {position.qty} @ {position.entry_price:.2f} "
                          f"SL={position.stop_loss_price:.2f} TP={position.take_profit_price:.2f}{note}")
                elif position is not None and ex_pos is None:
                    reason = pending_exit.info["reason"] if pending_exit else "closed_on_exchange"
                    exit_price = pending_exit.info["price"] if pending_exit else price
                    exit_is_maker = pending_exit.info["maker"] if pending_exit else False
                    print(f"    Position is closed on the exchange ({reason}) - settling from Shark's records")
                    settled = sync.settle(position)
                    if settled:
                        book_settled(position, reason, settled)
                    else:
                        print("    WARNING: Shark's trade history is incomplete - booking an ESTIMATE instead "
                              "(check the Shark app for the real result)")
                        close_out(position, reason + "_estimated", exit_price, exit_is_maker)
                    sync.cancel_all_resting()
                    position, pending_exit = None, None
                elif position is not None and abs(ex_pos["qty"] - position.qty) > 1e-9:
                    print(f"    Exchange size {ex_pos['qty']} differs from tracked {position.qty} - using exchange size")
                    position.qty = ex_pos["qty"]
                    position.entry_notional = position.entry_price * position.qty
                    sync.cancel_stops()  # re-placed below at the right size

                if pending_entry is not None and position is None and pending_entry.age() > order_timeout:
                    print(f"    Entry order not filled after {order_timeout}s - cancelling it")
                    if pending_entry.client_order_id:
                        exchange.cancel_order(pending_entry.client_order_id)
                    else:
                        sync.cancel_all_resting()
                    pending_entry = None  # a fill that raced the cancel is adopted next loop

                if position is not None and pending_exit is None:
                    try:
                        msg = sync.ensure_stop(position)
                        if msg:
                            print(f"    {msg}")
                    except Exception as e:
                        print(f"WARNING: position has NO exchange stop-loss ({e}) - retrying next loop")

                if pending_exit is not None and position is not None and pending_exit.age() > order_timeout:
                    print(f"    Exit order not filled after {order_timeout}s - escalating to MARKET")
                    sync.cancel_all_resting()
                    executor.place_market_order("SELL" if position.side == "LONG" else "BUY",
                                                position.qty, reduce_only=True)
                    pending_exit = Pending(None, reason=pending_exit.info["reason"], price=price, maker=False)

            if position is not None:
                held = bars_held(closed, position.opened_at_ms, interval_ms)
                bar_time = view[-1].get("time")
                if strategy.acts_on_closed_candles and (held < 1 or bar_time == last_exit_check):
                    decision = None                    # no candle has closed since entry / this one is already judged
                else:
                    decision = strategy.exit_decision(position, view[-1], indicators, held)
                    last_exit_check = bar_time
                if decision is not None:
                    reason, exit_price, exit_is_maker = decision
                    close_side = "SELL" if position.side == "LONG" else "BUY"
                    if real and pending_exit is not None:
                        pass  # an exit is already working; reconciliation books it when it fills
                    else:
                        if real:
                            sync.cancel_stops()  # the exit order replaces the resting stop
                        if exit_is_maker:
                            print(f">>> Exit ({reason}) at {exit_price:.2f} via LIMIT (maker)")
                            resp = executor.place_limit_order(close_side, position.qty, exit_price, reduce_only=True)
                        else:
                            print(f">>> Exit ({reason}) at {exit_price:.2f} via MARKET (taker)")
                            resp = executor.place_market_order(close_side, position.qty, reduce_only=True)
                        if real:
                            pending_exit = Pending((resp or {}).get("clientOrderId"), reason=reason,
                                                   price=exit_price, maker=exit_is_maker)
                        else:
                            close_out(position, reason, exit_price, exit_is_maker)
                            position = None
                            clear_dry()
            else:
                bar_time = view[-1].get("time")
                if real and (pending_entry is not None or foreign is not None):
                    plan = None
                elif strategy.acts_on_closed_candles and bar_time == last_entry_check:
                    plan = None                        # this closed candle was already judged
                else:
                    plan = strategy.entry_plan(view[-1]["close"] if strategy.acts_on_closed_candles else price,
                                               view[-1], indicators)
                    last_entry_check = bar_time
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
                                resp = executor.place_limit_order(open_side, qty, plan.entry_price, reduce_only=False)
                            else:
                                resp = executor.place_market_order(open_side, qty, reduce_only=False)
                            if real:
                                # Not a position until the exchange shows one.
                                pending_entry = Pending((resp or {}).get("clientOrderId"), plan=plan, qty=qty)
                                time.sleep(2)  # a market order fills at once - confirm without waiting a full poll
                                ex_pos = sync.position()
                                if ex_pos is not None and sync.owns(ex_pos):
                                    position = sync.adopt(ex_pos, plan)
                                    pending_entry = None
                                    print(f"    Confirmed on exchange: {position.side} {position.qty} @ "
                                          f"{position.entry_price:.2f} SL={position.stop_loss_price:.2f} "
                                          f"TP={position.take_profit_price:.2f}")
                                else:
                                    print(f"    Order {pending_entry.client_order_id} resting, not filled yet "
                                          f"(will cancel after {order_timeout}s)")
                            else:
                                position = Position(
                                    entry_price=plan.entry_price, qty=qty,
                                    entry_notional=plan.entry_price * qty,
                                    stop_loss_price=plan.stop_loss_price,
                                    take_profit_price=plan.take_profit_price,
                                    entry_is_maker=plan.is_maker,
                                    side=plan.side,
                                    opened_at_ms=int(time.time() * 1000),
                                )
                                publish_dry(position)
                                print(f"    entry={position.entry_price:.2f} SL={position.stop_loss_price:.2f} "
                                      f"TP={position.take_profit_price:.2f}")

            strategy.note_indicators(indicators)

        except requests.exceptions.HTTPError as err:
            body = err.response.text if err.response is not None else str(err)
            print(f"HTTP error: {body}")
        except Exception as e:
            print(f"Unexpected error: {e}")

        time.sleep(poll_seconds)
