"""Risk management: position sizing, daily loss halt, consecutive-loss cooldown.

Used by the live engine for both strategies. The backtester uses
calc_position_size() directly rather than the full RiskManager, since
its daily-loss/cooldown halts are wall-clock-based (date.today()) and
don't have a meaningful translation onto a fast-forwarded run over
historical candles spanning many real days. The backtester also does not
model exchange min-notional/leverage constraints (see
calc_leveraged_position_size) - a known simplification, same category as
the backtester's existing no-slippage assumption.
"""

import math
from datetime import date, datetime


def calc_position_size(entry_price: float, stop_loss_price: float,
                        risk_per_trade_pct: float, account_capital: float) -> float:
    risk_amount = (risk_per_trade_pct / 100.0) * account_capital
    stop_distance = abs(entry_price - stop_loss_price)
    return risk_amount / stop_distance if stop_distance > 0 else 0.0


def calc_leveraged_position_size(
    entry_price: float, stop_loss_price: float, risk_per_trade_pct: float,
    account_capital: float, leverage: float, min_notional: float, min_qty: float,
    quantity_precision: int, allow_min_notional_override: bool,
    quote_to_inr_rate: float = 1.0,
) -> tuple[float, dict]:
    """Min-notional/leverage-aware sizing for live trading.

    account_capital is always INR (project convention - see CLAUDE.md).
    entry_price/stop_loss_price/min_notional are in the TRADED SYMBOL'S
    QUOTE CURRENCY, which is only INR for INR-quoted pairs (BTCINR,
    XAUINR) - for a USDT-quoted pair like BTCUSDT, they're in USDT.
    quote_to_inr_rate converts 1 unit of quote currency to INR (pass 1.0
    for an INR-quoted symbol; pass the live INR/USDT rate etc. otherwise)
    - get it wrong and every number this function returns is wrong by
    that same factor. See main.py's cmd_run for how it's derived.

    Leverage does NOT reduce risk - the INR lost if a stop-loss hits is
    always (qty * stop_distance) converted to INR, regardless of
    leverage. Leverage only determines how much MARGIN (in INR - the
    exchange's marginAsset is always "INR", confirmed in CLAUDE.md) is
    required to hold a given qty. The real risk to the configured
    risk_per_trade_pct comes from the exchange's min_notional: if the
    risk-sized qty's notional is below it, qty must be forced up to
    clear the minimum, which inflates the actual INR-at-risk on that
    trade to something bigger than intended.

    By default (allow_min_notional_override=False) a trade that can't be
    sized within the intended risk_per_trade_pct while still clearing
    min_notional is refused (qty=0) rather than silently taking on more
    risk than configured. Only if the caller has explicitly opted into
    allow_min_notional_override does this size up to the minimum instead
    and report the real effective risk % in the returned info dict.

    Returns (qty, info). qty is 0.0 if the trade isn't tradeable; info
    always has a "reason" key when qty is 0.0. All INR-denominated info
    values are suffixed "_inr" to keep quote-currency vs INR unambiguous.
    """
    stop_distance = abs(entry_price - stop_loss_price)
    if stop_distance <= 0 or entry_price <= 0 or quote_to_inr_rate <= 0:
        return 0.0, {"reason": "invalid_price_inputs"}

    account_capital_quote = account_capital / quote_to_inr_rate
    risk_amount_quote = (risk_per_trade_pct / 100.0) * account_capital_quote
    ideal_qty = risk_amount_quote / stop_distance
    ideal_notional_quote = ideal_qty * entry_price

    qty = ideal_qty
    forced_by_min_notional = False
    if min_notional > 0 and ideal_notional_quote < min_notional:
        forced_by_min_notional = True
        if not allow_min_notional_override:
            return 0.0, {
                "reason": "below_min_notional_override_disabled",
                "ideal_notional_inr": ideal_notional_quote * quote_to_inr_rate,
                "min_notional_inr": min_notional * quote_to_inr_rate,
            }
        qty = min_notional / entry_price

    qty = max(qty, min_qty)
    step = 10 ** quantity_precision
    if forced_by_min_notional:
        # Round UP here - rounding down could truncate qty back below
        # min_notional (e.g. 0.0014974 -> floor to 0.001 given precision=3
        # would put notional under the minimum again, defeating the point
        # of forcing qty up to clear it in the first place).
        qty = math.ceil(qty * step) / step
    else:
        # Round DOWN for ordinary risk-based sizing - never round up past
        # what the risk math actually called for.
        qty = math.floor(qty * step) / step
    if qty <= 0:
        return 0.0, {"reason": "qty_rounds_to_zero"}

    notional_quote = qty * entry_price
    margin_required_inr = (notional_quote / leverage if leverage > 0 else notional_quote) * quote_to_inr_rate
    if margin_required_inr > account_capital:
        return 0.0, {
            "reason": "insufficient_margin",
            "margin_required_inr": margin_required_inr,
            "account_capital": account_capital,
            "leverage": leverage,
        }

    effective_risk_amount_inr = qty * stop_distance * quote_to_inr_rate
    effective_risk_pct = effective_risk_amount_inr / account_capital * 100
    return qty, {
        "notional_inr": notional_quote * quote_to_inr_rate,
        "margin_required_inr": margin_required_inr,
        "risk_intended_pct": risk_per_trade_pct,
        "risk_effective_pct": effective_risk_pct,
        "forced_by_min_notional": forced_by_min_notional,
    }


class RiskManager:
    def __init__(self, account_capital: float, risk_per_trade_pct: float,
                 max_daily_loss_pct: float, max_consecutive_losses: int,
                 cooldown_minutes_after_halt: int, leverage: float = 1.0,
                 min_notional: float = 0.0, min_qty: float = 0.0,
                 quantity_precision: int = 6, allow_min_notional_override: bool = False,
                 quote_to_inr_rate: float = 1.0):
        self.account_capital = account_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_minutes_after_halt = cooldown_minutes_after_halt
        self.leverage = leverage
        self.min_notional = min_notional
        self.min_qty = min_qty
        self.quantity_precision = quantity_precision
        self.allow_min_notional_override = allow_min_notional_override
        self.quote_to_inr_rate = quote_to_inr_rate

        self.day = date.today()
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.halted_until = None

    def _roll_day_if_needed(self):
        if date.today() != self.day:
            print(f"New day - resetting daily P&L (yesterday: {self.daily_pnl:.2f} INR)")
            self.day = date.today()
            self.daily_pnl = 0.0

    def can_open_new_trade(self) -> bool:
        self._roll_day_if_needed()

        if self.halted_until is not None:
            if datetime.now() < self.halted_until:
                return False
            print("Cooldown over - re-enabling trading.")
            self.halted_until = None
            self.consecutive_losses = 0

        max_daily_loss = -(self.max_daily_loss_pct / 100.0) * self.account_capital
        if self.daily_pnl <= max_daily_loss:
            print(f"Daily loss limit hit ({self.daily_pnl:.2f} <= {max_daily_loss:.2f} INR). "
                  f"No new trades today.")
            return False

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.halted_until = datetime.fromtimestamp(
                datetime.now().timestamp() + self.cooldown_minutes_after_halt * 60
            )
            print(f"{self.consecutive_losses} consecutive losses - pausing until "
                  f"{self.halted_until.strftime('%H:%M:%S')}.")
            return False

        return True

    def position_size(self, entry_price: float, stop_loss_price: float) -> tuple[float, dict]:
        """Returns (qty, info) via calc_leveraged_position_size - see there
        for what info contains and when qty comes back 0.0."""
        return calc_leveraged_position_size(
            entry_price, stop_loss_price, self.risk_per_trade_pct, self.account_capital,
            self.leverage, self.min_notional, self.min_qty, self.quantity_precision,
            self.allow_min_notional_override, self.quote_to_inr_rate,
        )

    def record_trade_result(self, gross_pnl_inr: float, fees_inr: float = 0.0):
        self._roll_day_if_needed()
        net = gross_pnl_inr - fees_inr
        self.daily_pnl += net
        if net < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        print(f"Trade closed. Gross P&L: {gross_pnl_inr:.2f} | Fees: {fees_inr:.2f} | "
              f"Net: {net:.2f} | Daily net P&L: {self.daily_pnl:.2f} | "
              f"Consecutive losses: {self.consecutive_losses}")
