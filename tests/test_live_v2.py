"""Live-engine behaviour needed to run closed-candle strategies like mean_reversion_v2 safely."""

from datetime import datetime, timezone

from shark.bot_registry import BotRegistry
from shark.intervals import bars_held
from shark.orders import fee_gate_passes
from shark.strategies.base import EntryPlan
from shark.strategies.mean_reversion_v2 import MeanReversionV2Strategy
from tests.test_live_reconcile import FakeExchange, FakeStrategy, pos, run_loop, stops

HOUR = 3_600_000


def ms(hh, mm=0):
    return int(datetime(2026, 10, 20, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


def candle(hh):
    return {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0, "time": ms(hh)}


class CandleExchange(FakeExchange):
    """Serves a fixed candle list: 11:00..14:00 closed, 15:00 still forming. Position opens 13:58:56."""

    def get_klines(self, symbol, interval, limit=100):
        super().get_klines(symbol, interval, limit)
        return [candle(h) for h in (11, 12, 13, 14, 15)]


class Recorder(FakeStrategy):
    def __init__(self, closed_mode):
        super().__init__()
        self.acts_on_closed_candles = closed_mode
        self.held, self.entry_candles = [], []

    def entry_plan(self, price, c, indicators):
        self.entry_candles.append(c["time"])
        return None

    def exit_decision(self, position, c, indicators, bars_held):
        self.held.append((bars_held, c["time"]))
        return None


def test_bars_held_counts_closed_candles_not_polls():
    ex = CandleExchange()
    ex.max_iterations = 5
    ex.position = pos("LONG", 0.5, 100.0)
    strat, *_ = run_loop(ex, Recorder(False))
    # 13:00 and 14:00 candles closed after the 13:58 entry; the same on every poll
    assert [h for h, _ in strat.held] == [2] * 5


def test_helper_counts_only_candles_closed_after_open():
    cs = [candle(h) for h in (11, 12, 13, 14)]
    assert bars_held(cs, ms(13, 58), HOUR) == 2
    assert bars_held(cs, ms(14, 0), HOUR) == 1
    assert bars_held(cs, None, HOUR) == 0


def test_closed_candle_strategy_judges_each_candle_once_on_closed_data():
    ex = CandleExchange()
    ex.max_iterations = 5
    strat, *_ = run_loop(ex, Recorder(True))
    assert strat.entry_candles == [ms(14)]          # last CLOSED candle, once - never the forming 15:00 one


def test_forming_candle_strategy_still_sees_every_poll():
    ex = CandleExchange()
    ex.max_iterations = 5
    strat, *_ = run_loop(ex, Recorder(False))
    assert strat.entry_candles == [ms(15)] * 5


def test_closed_candle_exit_waits_for_a_candle_to_close_after_entry():
    ex = CandleExchange()
    ex.max_iterations = 4
    ex.position = dict(pos("LONG", 0.5, 100.0), createdAt="2026-10-20T15:10:00.000Z")   # opened inside the forming 15:00 candle
    strat, *_ = run_loop(ex, Recorder(True))
    assert strat.held == []


def test_closed_candle_exit_judged_once_per_candle():
    ex = CandleExchange()
    ex.max_iterations = 5
    ex.position = pos("LONG", 0.5, 100.0)
    strat, *_ = run_loop(ex, Recorder(True))
    assert strat.held == [(2, ms(14))]


V2 = {"kline_interval": "4h", "bb_period": 20, "bb_stddev": 2.0, "rsi_period": 14, "rsi_oversold": 30,
      "rsi_overbought": 70, "atr_period": 14, "er_period": 20, "er_max": 0.3, "stop_atr_mult": 4.0,
      "max_bars": 24, "require_reversal": False, "allow_short": True, "min_profit_to_fee_ratio": 1.5,
      "fallback_stop_pct": 2.0}


class TestRestartWithV2Position:
    def test_saved_plan_survives_restart(self, tmp_path):
        path = str(tmp_path / "state.json")
        first = BotRegistry(path)
        first.set_plan("p1", 95.0, 110.0)
        ex = CandleExchange()
        ex.position = pos("LONG", 0.5, 100.0)
        run_loop(ex, MeanReversionV2Strategy(dict(V2, kline_interval="1h")), registry=BotRegistry(path))
        assert stops(ex)[0]["stopPrice"] == 95.0     # the ATR stop it was opened with, not a recomputed one

    def test_no_saved_plan_uses_fallback_instead_of_crashing(self):
        ex = CandleExchange()
        ex.position = pos("LONG", 0.5, 100.0)
        run_loop(ex, MeanReversionV2Strategy(dict(V2, kline_interval="1h")))
        assert stops(ex)[0]["stopPrice"] == 98.0

    def test_fresh_entry_saves_its_plan(self):
        ex = CandleExchange()
        ex.fill_on_place = True
        strat = FakeStrategy()
        strat.entry = EntryPlan(entry_price=100.0, stop_loss_price=96.0, take_profit_price=104.0,
                                is_maker=False, side="LONG")
        reg = BotRegistry(None)
        run_loop(ex, strat, registry=reg, own_p1=False)
        assert reg.plan_for("p1") == {"sl": 96.0, "tp": 104.0}

    def test_plan_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "s.json")
        BotRegistry(path).set_plan("p9", 1.5, 2.5)
        assert BotRegistry(path).plan_for("p9") == {"sl": 1.5, "tp": 2.5}


def test_fee_gate_prices_a_taker_entry():
    plan = EntryPlan(entry_price=100.0, stop_loss_price=99.0, take_profit_price=100.2, is_maker=True, side="LONG")
    assert fee_gate_passes(plan, 0.02, 0.05, 3.0)                        # 0.04% round trip x3 = 0.12 <= 0.2
    taker = EntryPlan(100.0, 99.0, 100.2, is_maker=False, side="LONG")
    assert not fee_gate_passes(taker, 0.02, 0.05, 3.0)                   # 0.07% x3 = 0.21 > 0.2


def test_klines_carry_candle_start_time(monkeypatch):
    from shark import exchange as exmod

    class R:
        def raise_for_status(self): pass
        def json(self): return [{"startTime": 1000, "open": "1", "high": "2", "low": "1", "close": "2"}]
    monkeypatch.setattr(exmod.requests, "post", lambda *a, **k: R())
    assert exmod.ExchangeClient("https://x").get_klines("SOLUSDT", "1h", 1)[0]["time"] == 1000


def test_dry_run_publishes_and_clears_the_simulated_position(tmp_path, monkeypatch):
    """The dashboard reads this file to show unrealised P&L in dry run."""
    import json
    from shark import live_engine
    from shark.orders import OrderExecutor
    from tests.test_live_reconcile import CONFIG, FakeRisk, FakeTracker, Stop, SYMBOL
    monkeypatch.chdir(tmp_path)
    path = tmp_path / f"dry_position_{SYMBOL}.json"
    path.write_text("stale")
    seen = []

    class S(FakeStrategy):
        def __init__(self):
            super().__init__()
            self.entry = EntryPlan(100.0, 98.0, 104.0, False, "LONG")

        def entry_plan(self, price, c, ind):
            plan, self.entry = self.entry, None            # one entry only
            return plan

        def exit_decision(self, position, c, ind, held):
            seen.append(json.loads(path.read_text()))
            return ("take_profit", 104.0, True) if len(seen) == 2 else None

    ex = CandleExchange()
    ex.max_iterations = 5
    cfg = dict(CONFIG, dry_run=True)
    executor = OrderExecutor(ex, SYMBOL, True, registry=BotRegistry(None))
    try:
        live_engine.run(S(), ex, executor, FakeRisk(), FakeTracker(), cfg, quote_to_inr_rate=90.0)
    except Stop:
        pass
    assert seen[0]["side"] == "LONG" and seen[0]["rate"] == 90.0 and seen[0]["qty"] == 0.5
    assert not path.exists()                                # cleared when the position closed
