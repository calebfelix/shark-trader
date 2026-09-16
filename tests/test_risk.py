from datetime import date

import pytest

from shark.risk import RiskManager, calc_leveraged_position_size, calc_position_size


class TestCalcPositionSize:
    def test_basic_sizing(self):
        # risk 1% of 2000 = 20 INR risked; stop distance = 10 -> qty = 2
        qty = calc_position_size(entry_price=100.0, stop_loss_price=90.0,
                                  risk_per_trade_pct=1.0, account_capital=2000.0)
        assert qty == 2.0

    def test_zero_stop_distance_returns_zero(self):
        qty = calc_position_size(entry_price=100.0, stop_loss_price=100.0,
                                  risk_per_trade_pct=1.0, account_capital=2000.0)
        assert qty == 0.0

    def test_wider_stop_means_smaller_size(self):
        tight = calc_position_size(100.0, 95.0, 1.0, 2000.0)
        wide = calc_position_size(100.0, 50.0, 1.0, 2000.0)
        assert wide < tight


class TestCalcLeveragedPositionSize:
    def _kwargs(self, **overrides):
        base = dict(
            entry_price=100.0, stop_loss_price=98.0, risk_per_trade_pct=1.0,
            account_capital=2000.0, leverage=10.0, min_notional=0.0, min_qty=0.0,
            quantity_precision=3, allow_min_notional_override=False,
            quote_to_inr_rate=1.0,
        )
        base.update(overrides)
        return base

    def test_invalid_stop_distance_refuses(self):
        qty, info = calc_leveraged_position_size(**self._kwargs(stop_loss_price=100.0))
        assert qty == 0.0
        assert info["reason"] == "invalid_price_inputs"

    def test_below_min_notional_refuses_without_override(self):
        qty, info = calc_leveraged_position_size(
            **self._kwargs(min_notional=100_000.0, allow_min_notional_override=False))
        assert qty == 0.0
        assert info["reason"] == "below_min_notional_override_disabled"

    def test_below_min_notional_sizes_up_with_override(self):
        # min_notional must be strictly above the risk-sized ideal notional
        # (100 here: 1% of 2000 / stop_distance 2 * entry 100) to actually
        # force the branch - 1000 would land exactly on the boundary.
        qty, info = calc_leveraged_position_size(
            **self._kwargs(min_notional=1500.0, allow_min_notional_override=True))
        assert qty > 0.0
        assert info["forced_by_min_notional"] is True
        # Forced-up qty must clear min_notional even after precision rounding.
        assert qty * 100.0 >= 1500.0

    def test_min_notional_override_rounds_up_not_down(self):
        # Regression for the real bug documented in CLAUDE.md: rounding a
        # forced-up qty DOWN to quantity_precision could truncate it back
        # below min_notional. 115 / 76813.60 = 0.0014974..., which floors
        # to 0.001 at precision 3 (notional 76.81, still under 115). Stop
        # distance is set wide so the risk-sized ideal qty is naturally
        # tiny, actually forcing the min-notional branch.
        qty, info = calc_leveraged_position_size(
            entry_price=76813.60, stop_loss_price=75813.60, risk_per_trade_pct=1.0,
            account_capital=2000.0, leverage=30.0, min_notional=115.0, min_qty=0.001,
            quantity_precision=3, allow_min_notional_override=True, quote_to_inr_rate=95.0,
        )
        assert info["forced_by_min_notional"] is True
        assert qty >= 0.002  # ceil(0.0014974 * 1000) / 1000 == 0.002, not 0.001
        assert qty * 76813.60 >= 115.0

    def test_insufficient_margin_refuses(self):
        # margin_required/account_capital = (risk_pct/100) * (entry/stop_distance)
        # / leverage, which is scale-invariant in account_capital - a low
        # enough leverage is what forces this, not a small account_capital.
        qty, info = calc_leveraged_position_size(**self._kwargs(leverage=0.1))
        assert qty == 0.0
        assert info["reason"] == "insufficient_margin"

    def test_currency_conversion_scales_correctly(self):
        # Same INR risk budget, but priced in a quote currency worth 90x
        # less per unit than INR (e.g. USDT vs INR) - risk_effective_pct
        # should land at roughly the same % of account_capital regardless.
        # High precision minimizes floor-rounding noise between the two
        # scales so the comparison isn't dominated by rounding artifacts.
        qty_inr, info_inr = calc_leveraged_position_size(
            **self._kwargs(quote_to_inr_rate=1.0, quantity_precision=8))
        qty_quote, info_quote = calc_leveraged_position_size(
            **self._kwargs(entry_price=100.0 / 90.0, stop_loss_price=98.0 / 90.0,
                            quote_to_inr_rate=90.0, quantity_precision=8))
        assert info_inr["risk_effective_pct"] == pytest.approx(info_quote["risk_effective_pct"], rel=1e-3)

    def test_effective_risk_matches_intended_when_not_forced(self):
        qty, info = calc_leveraged_position_size(**self._kwargs())
        assert info["forced_by_min_notional"] is False
        assert round(info["risk_effective_pct"], 4) == round(info["risk_intended_pct"], 4)


class TestRiskManager:
    def _manager(self, **overrides):
        base = dict(
            account_capital=2000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=3.0,
            max_consecutive_losses=4, cooldown_minutes_after_halt=60,
        )
        base.update(overrides)
        return RiskManager(**base)

    def test_can_open_trade_initially(self):
        rm = self._manager()
        assert rm.can_open_new_trade() is True

    def test_daily_loss_limit_blocks_new_trades(self):
        rm = self._manager(max_daily_loss_pct=3.0)  # 3% of 2000 = 60 INR
        rm.record_trade_result(gross_pnl_inr=-70.0)
        assert rm.can_open_new_trade() is False

    def test_daily_loss_limit_not_yet_hit_allows_trades(self):
        rm = self._manager(max_daily_loss_pct=3.0)
        rm.record_trade_result(gross_pnl_inr=-10.0)
        assert rm.can_open_new_trade() is True

    def test_consecutive_losses_trigger_cooldown(self):
        rm = self._manager(max_consecutive_losses=3, max_daily_loss_pct=1000.0)
        for _ in range(3):
            rm.record_trade_result(gross_pnl_inr=-1.0)
        assert rm.consecutive_losses == 3
        assert rm.can_open_new_trade() is False
        assert rm.halted_until is not None

    def test_win_resets_consecutive_loss_counter(self):
        rm = self._manager()
        rm.record_trade_result(gross_pnl_inr=-1.0)
        rm.record_trade_result(gross_pnl_inr=-1.0)
        rm.record_trade_result(gross_pnl_inr=+5.0)
        assert rm.consecutive_losses == 0

    def test_fees_count_against_net_pnl(self):
        rm = self._manager(max_daily_loss_pct=3.0)  # 60 INR limit
        # Gross profitable, but fees push it net-negative enough to halt.
        rm.record_trade_result(gross_pnl_inr=10.0, fees_inr=80.0)
        assert rm.daily_pnl == -70.0
        assert rm.can_open_new_trade() is False

    def test_new_day_resets_daily_pnl(self):
        rm = self._manager()
        rm.record_trade_result(gross_pnl_inr=-50.0)
        assert rm.daily_pnl == -50.0
        rm.day = date(2000, 1, 1)  # force a stale "day" so the next check rolls over
        rm.can_open_new_trade()
        assert rm.daily_pnl == 0.0
