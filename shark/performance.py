"""Session performance tracking - same report shape for dry-run or live,
for every strategy. Every closed trade is appended to a CSV, with a
running console summary after each trade and a final summary on exit.
"""

import csv
import os
from datetime import datetime


class PerformanceTracker:
    def __init__(self, csv_path: str, starting_capital: float, symbol: str,
                 print_summary_every_n_trades: int = 1):
        self.csv_path = csv_path
        self.starting_capital = starting_capital
        self.symbol = symbol
        self.print_summary_every_n_trades = print_summary_every_n_trades
        self.equity = starting_capital
        self.trades = []
        self.session_start = datetime.now()

        self._ensure_csv()

    # Legacy columns first (older files / readers keep working), the cost breakdown appended after them.
    COLUMNS = ["timestamp", "symbol", "entry_price", "exit_price", "qty", "exit_reason", "gross_pnl", "fees",
               "net_pnl", "equity_after", "side", "fee_discount", "funding", "source", "entry_role",
               "exit_role", "position_id"]

    def _ensure_csv(self):
        """Create the CSV, or upgrade an older one (fewer columns) in place without losing rows."""
        if os.path.exists(self.csv_path):
            with open(self.csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
                f.seek(0)
                header = next(csv.reader(f), [])
            if header == self.COLUMNS:
                return
        else:
            rows = []
        with open(self.csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def record(self, entry_price, exit_price, qty, exit_reason, gross_pnl, fees,
               discount=0.0, funding=0.0, extra=None):
        """net = gross - fees + fee discount + funding (funding is signed: paid < 0). `fees` is the trading
        fee BEFORE discount, so every cost shows up in its own column."""
        net_pnl = gross_pnl - fees + discount + funding
        self.equity += net_pnl
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "symbol": self.symbol,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "exit_reason": exit_reason,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "net_pnl": net_pnl,
            "equity_after": self.equity,
            "side": "", "fee_discount": discount, "funding": funding, "source": "",
            "entry_role": "", "exit_role": "", "position_id": "",
        }
        row.update(extra or {})
        self.trades.append(row)

        with open(self.csv_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.COLUMNS, extrasaction="ignore").writerow(row)

        if len(self.trades) % self.print_summary_every_n_trades == 0:
            self.print_summary()

    def print_summary(self):
        n = len(self.trades)
        if n == 0:
            print("\n[Performance] No trades closed yet this session.")
            return

        wins = [t for t in self.trades if t["net_pnl"] > 0]
        losses = [t for t in self.trades if t["net_pnl"] <= 0]
        gross_pnl = sum(t["gross_pnl"] for t in self.trades)
        total_fees = sum(t["fees"] for t in self.trades)
        net_pnl = sum(t["net_pnl"] for t in self.trades)
        win_rate = len(wins) / n * 100
        avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
        gross_win = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        runtime = datetime.now() - self.session_start

        print("\n" + "-" * 60)
        print(f"[Performance] Session runtime: {runtime}  |  Trades: {n}")
        print(f"  Starting equity: {self.starting_capital:.2f} INR  ->  "
              f"Current: {self.equity:.2f} INR ({net_pnl / self.starting_capital * 100:+.2f}%)")
        total_disc = sum(t["fee_discount"] for t in self.trades)
        total_fund = sum(t["funding"] for t in self.trades)
        print(f"  Gross P&L: {gross_pnl:+.2f} | Fees paid: {total_fees:.2f} | Fee discounts: +{total_disc:.2f} | "
              f"Funding: {total_fund:+.2f} | Net P&L: {net_pnl:+.2f}")
        print(f"  Win rate: {win_rate:.1f}% ({len(wins)}W/{len(losses)}L) | "
              f"Avg win: {avg_win:+.2f} | Avg loss: {avg_loss:+.2f} | "
              f"Profit factor: {profit_factor:.2f}")
        print(f"  Full trade log: {self.csv_path}")
        print("-" * 60)

    def print_final_summary(self, dry_run: bool):
        print("\n" + "=" * 60)
        print("FINAL SESSION SUMMARY" + (" (DRY RUN - no real orders were placed)" if dry_run else " (LIVE)"))
        print("=" * 60)
        self.print_summary()
