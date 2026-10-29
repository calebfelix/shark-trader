"""macd_multi: the multi-entry book, its 4h trend timing, the dry-run engine, and the real-mode refusals."""

import argparse
import json

import pytest

from shark import multi_leg_engine
from shark.strategies.macd_multi import H4_MS, M30_MS, MacdMultiStrategy, MultiEntryBook

P = {"kline_interval": "30m", "trend_interval": "4h", "ema_period": 200, "macd_fast": 12, "macd_slow": 26,
     "macd_signal": 9, "max_entries": 3, "tp_r": [0.25, 0.25, 0.5], "min_stop_pct": 3.0, "min_gap_frac": 0.1,
     "history_30m": 400, "history_4h": 1000}


def book(slip=0.0):
    return MultiEntryBook(P, slip)


def take(b, side, close, ema, qty=1.0):
    leg, why = b.plan_entry(side, close, ema)
    assert leg, why
    leg["qty"] = qty
    b.add(leg)
    return leg


def candle(o, h, l, c, t=0):
    return {"open": o, "high": h, "low": l, "close": c, "time": t}


class TestSignal:
    def test_long_needs_cross_up_below_zero_above_ema(self):
        b = book()
        assert b.signal(-2, -1, -0.5, -0.8, 110, 100) == "LONG"
        assert b.signal(-2, -1, 0.5, 0.2, 110, 100) is None        # crossed above zero: not the setup
        assert b.signal(-2, -1, -0.5, -0.8, 90, 100) is None       # against the 4h trend

    def test_short_mirror(self):
        b = book()
        assert b.signal(2, 1, 0.5, 0.8, 90, 100) == "SHORT"
        assert b.signal(2, 1, -0.5, -0.2, 90, 100) is None


class TestEntries:
    def test_first_entry_uses_ema_as_stop_and_quarter_r_target(self):
        leg = take(book(), "LONG", 104.0, 100.0)
        assert leg["stop"] == 100.0 and leg["n"] == 1
        assert leg["tp"] == pytest.approx(104.0 + 0.25 * 4.0)

    def test_tight_setup_skipped(self):
        leg, why = book().plan_entry("LONG", 102.0, 100.0)          # 1.96% < 3%
        assert leg is None and "3.0%" in why

    def test_add_ons_must_improve_by_a_gap_and_stop_at_three(self):
        b = book()
        take(b, "LONG", 110.0, 100.0)
        assert b.plan_entry("LONG", 111.0, 100.0)[0] is None         # worse price
        assert b.plan_entry("LONG", 109.5, 100.0)[0] is None         # < 10% of the 10-point stop distance away
        assert b.plan_entry("SHORT", 108.0, 100.0)[0] is None        # opposite side
        second = take(b, "LONG", 108.0, 100.0)
        third = take(b, "LONG", 106.0, 100.0)
        assert second["stop"] == third["stop"] == 100.0               # shared stop, fixed at the first entry
        assert second["tp"] == pytest.approx(108.0 + 0.25 * 8.0)
        assert third["tp"] == pytest.approx(106.0 + 0.5 * 6.0)       # third entry targets 0.5R
        assert b.plan_entry("LONG", 104.0, 100.0)[0] is None         # max three

    def test_closed_entry_still_counts_toward_the_three(self):
        b = book()
        take(b, "LONG", 110.0, 100.0)
        take(b, "LONG", 108.0, 100.0)                                 # tp 110
        b.exits(candle(108, 110.2, 107.9, 110))                       # entry 2 hits its target, entry 1 (tp 112.5) open
        assert len(b.legs) == 1
        third = take(b, "LONG", 106.0, 100.0)
        assert third["n"] == 3
        assert b.plan_entry("LONG", 104.0, 100.0)[0] is None         # only 2 open, but 3 were taken

    def test_slippage_on_entry(self):
        leg = take(book(slip=0.02), "LONG", 104.0, 100.0)
        assert leg["entry"] == pytest.approx(104.0 * 1.0002)


class TestExits:
    def test_stop_checked_before_target_and_gaps_fill_at_open(self):
        b = book()
        take(b, "LONG", 104.0, 100.0)
        (leg, reason, px, maker), = b.exits(candle(99, 106, 98, 105))   # gaps below the stop, also tags the target
        assert reason == "stop_loss" and px == 99 and maker is False
        assert b.legs == [] and b.setup_stop is None and b.taken == 0

    def test_target_is_a_maker_fill_at_its_level(self):
        b = book()
        take(b, "SHORT", 96.0, 100.0)
        (leg, reason, px, maker), = b.exits(candle(96, 96.5, 94.9, 95.2))
        assert reason == "take_profit" and px == pytest.approx(95.0) and maker

    def test_one_stop_closes_every_entry(self):
        b = book()
        for px in (110.0, 108.0, 106.0):
            take(b, "LONG", px, 100.0)
        assert [r for _, r, *_ in b.exits(candle(101, 101, 99, 99.5))] == ["stop_loss"] * 3

    def test_aggregate_is_qty_weighted(self):
        b = book()
        take(b, "LONG", 110.0, 100.0, qty=1.0)
        take(b, "LONG", 108.0, 100.0, qty=3.0)
        a = b.aggregate()
        assert a["qty"] == 4.0 and a["entry"] == pytest.approx((110 + 3 * 108) / 4) and a["legs"] == 2
        assert a["tp"] == pytest.approx(110.0)                        # the nearest target


def test_4h_trend_value_is_only_known_after_that_candle_closes():
    s = MacdMultiStrategy(dict(P, ema_period=2))
    c4 = [{"close": float(x), "time": i * H4_MS} for i, x in enumerate([10, 10, 10, 20])]
    c30 = [{"close": 1.0, "time": t} for t in (3 * H4_MS - M30_MS, 3 * H4_MS, 4 * H4_MS - M30_MS)]
    out = s.series(c30, c4)
    # the 30m candle closing at 3*H4 sees 4h candle #2's EMA (still 10); the 20 close only counts at 4*H4
    assert out[0][2] == pytest.approx(10.0) and out[1][2] == pytest.approx(10.0)
    assert out[2][2] > 10.0


class Stop(BaseException):
    pass


class FakeExchange:
    """The first poll serves `history` closed 30m candles, each later poll one more (plus a forming one)."""

    def __init__(self, scripted, history=3):
        self.scripted, self.polls, self.history = scripted, 0, history

    def get_klines(self, symbol, interval, limit):
        if interval == "4h":
            return [{"open": 1, "high": 1, "low": 1, "close": 1, "time": 0}] * 2
        self.polls += 1
        n = self.history + self.polls - 1
        if n > len(self.scripted):
            raise Stop()
        return self.scripted[:n] + [candle(1, 1, 1, 1, t=10 ** 12)]


class FakeRisk:
    def __init__(self): self.results = []
    def can_open_new_trade(self): return True
    def position_size(self, entry, stop): return 0.5, {}
    def record_trade_result(self, gross, fees): self.results.append((gross, fees))


class FakeTracker:
    def __init__(self): self.rows = []
    def record(self, entry, exit_, qty, reason, gross, fees, extra=None):
        self.rows.append({"entry": entry, "exit": exit_, "qty": qty, "reason": reason, "gross": gross,
                          "fees": fees, **(extra or {})})


CFG = {"symbol": "SOLUSDT", "dry_run": True, "poll_seconds": 0, "account_capital": 10000, "risk_per_trade_pct": 1,
       "fees": {"maker_fee_pct": 0.016, "taker_fee_pct": 0.04, "gst_pct": 18}, "backtest": {"slippage_pct": 0.0}}


def test_engine_enters_on_new_candles_only_and_books_the_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(multi_leg_engine.time, "sleep", lambda s: None)
    candles = [candle(106, 106, 106, 106, t=i * M30_MS) for i in range(3)]   # history at start
    candles += [candle(104, 104.5, 103.5, 104, t=3 * M30_MS),             # signal candle -> entry @104
                candle(104, 105.2, 103.9, 105, t=4 * M30_MS)]             # tags the 105 target
    strat = MacdMultiStrategy(P)
    # MACD crosses up (below zero) on candle 1 - history, closed before the start, must be ignored - and on
    # candle 3, the first new one.
    cross = {1 * M30_MS, 3 * M30_MS}
    monkeypatch.setattr(strat, "series", lambda c30, c4: [
        (-0.5, -0.8, 100.0) if c["time"] in cross else (-2.0, -1.0, 100.0) for c in c30])
    seen_state = []
    real_publish_path = tmp_path / "dry_position_SOLUSDT.json"
    orig_exits = MultiEntryBook.exits

    def spy(self, c):
        if real_publish_path.exists():
            seen_state.append(json.loads(real_publish_path.read_text()))
        return orig_exits(self, c)
    monkeypatch.setattr(MultiEntryBook, "exits", spy)
    tracker, risk = FakeTracker(), FakeRisk()
    with pytest.raises(Stop):
        multi_leg_engine.run(strat, FakeExchange(candles), risk, tracker, CFG, quote_to_inr_rate=90.0)
    assert len(tracker.rows) == 1                                     # one entry, from the new candle only
    row = tracker.rows[0]
    assert row["reason"] == "take_profit" and row["entry"] == 104 and row["exit"] == pytest.approx(105.0)
    assert row["source"] == "simulated" and row["exit_role"] == "maker" and row["position_id"] == "setup1-entry1"
    assert row["gross"] == pytest.approx(1.0 * 0.5 * 90.0)
    assert seen_state and all(st["legs"] == 1 for st in seen_state) and seen_state[0]["entry_price"] == 104
    assert not real_publish_path.exists()                             # cleared once flat


def test_engine_refuses_real_mode():
    with pytest.raises(SystemExit):
        multi_leg_engine.run(MacdMultiStrategy(P), None, None, None, dict(CFG, dry_run=False))


def test_main_refuses_real_mode_before_touching_the_exchange():
    import main
    cfg = dict(CFG, dry_run=False, base_url="https://x", api_key="k", api_secret="s", strategies={"macd_multi": P})
    with pytest.raises(SystemExit, match="dry-run only"):
        main.cmd_run(argparse.Namespace(strategy="macd_multi"), cfg)


def test_dashboard_refuses_live_start(monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard, "load_config", lambda: {"dry_run": False, "api_key": "k", "api_secret": "s"})
    ok, msg = dashboard.start_bot("macd_multi", confirm_live=True)
    assert not ok and "dry-run only" in msg
