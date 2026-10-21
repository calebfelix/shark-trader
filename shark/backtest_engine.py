"""Strategy-agnostic backtest engine.

Fill model (config backtest.realistic_fills, default true), based on what the live bot actually paid:
  * entries are marketable (limit set at/through the market, or a market order) -> TAKER fee, plus slippage;
  * stop and signal exits are market orders -> TAKER; a stop that gaps past its level fills at the worse
    price (the open), plus slippage; signal exits (RSI/mid-band/max-hold) are NOT free maker fills;
  * only a resting take-profit limit keeps its MAKER fee and fills at its level.
Set realistic_fills=false to get the old idealised numbers (everything fills at the intended price, makers
for anything a strategy calls maker) - those over-state results. Funding and leverage limits are not modelled.
"""

from dataclasses import dataclass
from typing import Optional

from .orders import effective_fee_pct, fee_gate_passes, round_trip_fee_estimate
from .risk import calc_position_size
from .strategies.base import EntryPlan, Position, Strategy


@dataclass
class Trade:
    entry_price: float
    qty: float
    entry_notional: float
    entry_is_maker: bool
    side: str
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl: Optional[float] = None
    fees: Optional[float] = None
    net_pnl: Optional[float] = None

    def close(self, exit_price: float, exit_reason: str, exit_is_maker: bool,
              maker_fee_pct: float, taker_fee_pct: float):
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        if self.side == "LONG":
            self.gross_pnl = (exit_price - self.entry_price) * self.qty
        else:
            self.gross_pnl = (self.entry_price - exit_price) * self.qty
        fee_pct = round_trip_fee_estimate(maker_fee_pct, taker_fee_pct,
                                           self.entry_is_maker, exit_is_maker)
        self.fees = self.entry_notional * fee_pct / 100
        self.net_pnl = self.gross_pnl - self.fees


STOP_REASONS = ("stop_loss", "psar_trailing_stop")


def realistic_exit(side: str, reason: str, price: float, is_maker: bool, candle_open: float,
                   slippage: float) -> tuple[float, bool]:
    """(fill price, is_maker) for an exit the strategy decided at `price`."""
    if reason == "take_profit" and is_maker:
        return price, True                         # resting limit, filled at its level
    if reason in STOP_REASONS:                     # market order after a trigger: a gap fills at the open
        price = min(price, candle_open) if side == "LONG" else max(price, candle_open)
    # closing a LONG sells (worse = lower), closing a SHORT buys (worse = higher)
    return price * (1 - slippage if side == "LONG" else 1 + slippage), False


def run_backtest(strategy: Strategy, candles: list[dict], config: dict) -> tuple[list[Trade], list[float]]:
    bt_cfg = config.get("backtest", {})
    realistic = bt_cfg.get("realistic_fills", True)
    slippage = bt_cfg.get("slippage_pct", 0.02) / 100.0 if realistic else 0.0
    fees_cfg = config["fees"]
    maker_fee_pct = effective_fee_pct(fees_cfg["maker_fee_pct"], fees_cfg["gst_pct"])
    taker_fee_pct = effective_fee_pct(fees_cfg["taker_fee_pct"], fees_cfg["gst_pct"])
    account_capital = config["account_capital"]
    risk_per_trade_pct = config["risk_per_trade_pct"]
    min_profit_to_fee_ratio = strategy.params.get("min_profit_to_fee_ratio", 0)

    candle_history: list[dict] = []
    trades: list[Trade] = []
    equity = account_capital
    equity_curve = [equity]

    position: Optional[Position] = None

    for idx, candle in enumerate(candles):
        candle_history.append(candle)
        indicators = strategy.compute_indicators(candle_history)

        if position is not None:
            bars_held = idx - position.opened_at
            decision = strategy.exit_decision(position, candle, indicators, bars_held)
            if decision is not None:
                reason, exit_price, exit_is_maker = decision
                if realistic:
                    exit_price, exit_is_maker = realistic_exit(position.side, reason, exit_price, exit_is_maker,
                                                               candle["open"], slippage)
                trade = Trade(entry_price=position.entry_price, qty=position.qty,
                               entry_notional=position.entry_notional,
                               entry_is_maker=position.entry_is_maker, side=position.side)
                trade.close(exit_price, reason, exit_is_maker, maker_fee_pct, taker_fee_pct)
                equity += trade.net_pnl
                trades.append(trade)
                position = None
        else:
            plan = strategy.entry_plan(candle["close"], candle, indicators)
            if plan is not None:
                gate_ok = (not strategy.fee_gate_enabled or
                           fee_gate_passes(plan, maker_fee_pct, taker_fee_pct, min_profit_to_fee_ratio))
                if gate_ok:
                    qty = calc_position_size(plan.entry_price, plan.stop_loss_price,
                                              risk_per_trade_pct, equity)
                    if qty > 0:
                        entry_fill, entry_maker = plan.entry_price, plan.is_maker
                        if realistic:                      # marketable entry: pays the taker fee, slips against us
                            entry_maker = False
                            entry_fill = plan.entry_price * (1 + slippage if plan.side == "LONG" else 1 - slippage)
                        position = Position(
                            entry_price=entry_fill, qty=qty,
                            entry_notional=entry_fill * qty,
                            stop_loss_price=plan.stop_loss_price,
                            take_profit_price=plan.take_profit_price,
                            entry_is_maker=entry_maker,
                            side=plan.side,
                            opened_at=idx,
                        )

        strategy.note_indicators(indicators)
        equity_curve.append(equity)

    if position is not None:
        trade = Trade(entry_price=position.entry_price, qty=position.qty,
                       entry_notional=position.entry_notional,
                       entry_is_maker=position.entry_is_maker, side=position.side)
        trade.close(candles[-1]["close"], "end_of_data", exit_is_maker=False,
                    maker_fee_pct=maker_fee_pct, taker_fee_pct=taker_fee_pct)
        equity += trade.net_pnl
        trades.append(trade)
        equity_curve.append(equity)

    return trades, equity_curve


def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


def print_report(strategy_name: str, symbol: str, interval: str,
                  trades: list[Trade], equity_curve: list[float], config: dict):
    account_capital = config["account_capital"]

    print("\n" + "=" * 60)
    print(f"BACKTEST REPORT - {strategy_name} - {symbol} {interval} - "
          f"{len(trades)} trades over {len(equity_curve) - 1} candles")
    print("=" * 60)

    if not trades:
        print("No trades were triggered. Try loosening entry thresholds, "
              "increasing lookback_candles, or a different symbol/interval.")
        return

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    gross_pnl = sum(t.gross_pnl for t in trades)
    total_fees = sum(t.fees for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)

    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    dd = max_drawdown(equity_curve)

    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    print(f"Starting capital:      {account_capital:.2f} INR")
    print(f"Ending capital:        {equity_curve[-1]:.2f} INR")
    print(f"Gross P&L:             {gross_pnl:+.2f} INR")
    print(f"Total fees paid:       {total_fees:.2f} INR  "
          f"({total_fees / abs(gross_pnl) * 100 if gross_pnl else 0:.1f}% of gross, if gross != 0)")
    print(f"Net P&L (after fees):  {net_pnl:+.2f} INR ({net_pnl / account_capital * 100:+.2f}%)")
    print("-" * 60)
    print(f"Total trades:          {len(trades)}")
    print(f"Win rate:              {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"Average win:           {avg_win:+.2f} INR")
    print(f"Average loss:          {avg_loss:+.2f} INR")
    print(f"Profit factor:         {profit_factor:.2f}  "
          f"(>1.0 = winners outweigh losers after fees)")
    print(f"Max drawdown:          {dd:.2f}%")
    print(f"Exit reasons:          {exit_reasons}")
    print("=" * 60)

    if net_pnl <= 0 <= gross_pnl:
        print("NOTE: Strategy was profitable BEFORE fees but not after - "
              "fees ate the edge. Consider raising min_profit_to_fee_ratio, "
              "widening take_profit_pct, or trading a less fee-sensitive setup.")


def plot_equity_curve(strategy_name: str, symbol: str, interval: str,
                       equity_curve: list[float], config: dict):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib not installed - skipping equity curve chart. "
              "Install with: pip install matplotlib)")
        return

    output_path = config["backtest"]["output_chart_path"]
    plt.figure(figsize=(10, 5))
    plt.plot(equity_curve, linewidth=1.5)
    plt.axhline(config["account_capital"], color="gray", linestyle="--", linewidth=1, label="Starting capital")
    plt.title(f"Equity Curve - {symbol} {interval} {strategy_name}")
    plt.xlabel("Candle #")
    plt.ylabel("Equity (INR)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\nEquity curve chart saved to: {output_path}")
