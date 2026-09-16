from shark.orders import effective_fee_pct, fee_gate_passes, round_trip_fee_estimate
from shark.strategies.base import EntryPlan


class TestEffectiveFeePct:
    def test_gst_applied_on_top(self):
        # 0.04% taker + 18% GST -> 0.04 * 1.18 = 0.0472
        assert round(effective_fee_pct(0.040, 18.0), 4) == 0.0472

    def test_zero_gst_is_a_noop(self):
        assert effective_fee_pct(0.040, 0.0) == 0.040


class TestRoundTripFeeEstimate:
    def test_maker_both_sides_cheaper_than_taker(self):
        maker_only = round_trip_fee_estimate(0.02, 0.05, entry_is_maker=True, exit_is_maker=True)
        taker_only = round_trip_fee_estimate(0.02, 0.05, entry_is_maker=False, exit_is_maker=False)
        assert maker_only < taker_only

    def test_mixed_sides_sums_correctly(self):
        result = round_trip_fee_estimate(0.02, 0.05, entry_is_maker=True, exit_is_maker=False)
        assert result == 0.02 + 0.05


class TestFeeGatePasses:
    def _long_plan(self, entry, tp):
        return EntryPlan(entry_price=entry, stop_loss_price=entry * 0.98,
                          take_profit_price=tp, is_maker=True, side="LONG")

    def _short_plan(self, entry, tp):
        return EntryPlan(entry_price=entry, stop_loss_price=entry * 1.02,
                          take_profit_price=tp, is_maker=True, side="SHORT")

    def test_long_plan_wide_enough_move_passes(self):
        plan = self._long_plan(entry=100.0, tp=110.0)  # 10% move
        assert fee_gate_passes(plan, maker_fee_pct=0.02, taker_fee_pct=0.05,
                                min_profit_to_fee_ratio=3.0) is True

    def test_long_plan_too_tight_fails(self):
        plan = self._long_plan(entry=100.0, tp=100.01)  # ~0.01% move
        assert fee_gate_passes(plan, maker_fee_pct=0.02, taker_fee_pct=0.05,
                                min_profit_to_fee_ratio=3.0) is False

    def test_short_plan_wide_enough_move_passes(self):
        # Regression: a SHORT plan's take_profit < entry_price. An earlier
        # version of this check computed (take_profit/entry - 1), which is
        # negative for a SHORT and would always fail this gate - see
        # CLAUDE.md's "Fee-gate bug fixed" note. abs() must make this
        # symmetric between LONG and SHORT.
        plan = self._short_plan(entry=100.0, tp=90.0)  # 10% move, wrong direction sign
        assert fee_gate_passes(plan, maker_fee_pct=0.02, taker_fee_pct=0.05,
                                min_profit_to_fee_ratio=3.0) is True

    def test_short_plan_too_tight_fails(self):
        plan = self._short_plan(entry=100.0, tp=99.99)
        assert fee_gate_passes(plan, maker_fee_pct=0.02, taker_fee_pct=0.05,
                                min_profit_to_fee_ratio=3.0) is False

    def test_long_and_short_with_symmetric_moves_agree(self):
        long_plan = self._long_plan(entry=100.0, tp=105.0)
        short_plan = self._short_plan(entry=100.0, tp=95.0)
        long_result = fee_gate_passes(long_plan, 0.02, 0.05, 3.0)
        short_result = fee_gate_passes(short_plan, 0.02, 0.05, 3.0)
        assert long_result == short_result
