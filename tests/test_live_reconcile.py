"""Real-account live loop: the exchange's position is the source of truth.
Drives the real live_engine.run() against a fake exchange - no network."""

import pytest

from shark import live_engine
from shark.exchange import ExchangeClient
from shark.orders import OrderExecutor
from shark.strategies.base import EntryPlan, Strategy

SYMBOL = "SOLUSDT"


class Stop(BaseException):
    """Ends the infinite loop (BaseException so the loop's `except Exception` can't swallow it)."""


class FakeExchange(ExchangeClient):
    """Real ExchangeClient parsing + a scriptable in-memory exchange behind signed_get/post."""

    def __init__(self):
        super().__init__("https://fake", "k", "s")
        self.position = None          # dict in Shark's response shape, or None
        self.orders = []              # resting orders
        self.placed = []              # every place-order body, in order
        self.cancelled = []
        self.fill_on_place = False    # market/limit entries fill immediately
        self.positions_error = None
        self.iterations = 0
        self.max_iterations = 3
        self.on_iteration = None      # callable(fake, iteration) to mutate state between loops
        self._n = 0

    def get_klines(self, symbol, interval, limit=100):
        self.iterations += 1
        if self.iterations > self.max_iterations:
            raise Stop()
        if self.on_iteration:
            self.on_iteration(self, self.iterations)
        return [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}] * 5

    def signed_get(self, endpoint, params=None):
        if endpoint.startswith("/v1/positions/"):
            if self.positions_error:
                raise self.positions_error
            return [self.position] if self.position else []
        if endpoint == "/v1/order/open-orders":
            return list(self.orders)
        raise AssertionError(endpoint)

    def signed_post(self, endpoint, params):
        assert endpoint == "/v1/order/place-order"
        self._n += 1
        cid = f"cid{self._n}"
        self.placed.append(dict(params, clientOrderId=cid))
        if params["type"] == "STOP_MARKET":
            self.orders.append({"clientOrderId": cid, "symbol": SYMBOL, "type": "STOP_MARKET",
                                "stopPrice": params["stopPrice"], "side": params["side"]})
        elif params["reduceOnly"] is False and self.fill_on_place:
            self.position = pos("LONG" if params["side"] == "BUY" else "SHORT", params["quantity"], 100.0)
        elif params["reduceOnly"] is False:
            self.orders.append({"clientOrderId": cid, "symbol": SYMBOL, "type": params["type"]})
        elif params["reduceOnly"] is True and params["type"] == "MARKET":
            self.position = None
        return {"clientOrderId": cid}

    def cancel_order(self, client_order_id):
        self.cancelled.append(client_order_id)
        self.orders = [o for o in self.orders if o["clientOrderId"] != client_order_id]
        return {"success": True}


def pos(side, qty, entry):
    return {"contractPair": SYMBOL, "positionType": side, "positionAmount": qty,
            "quantity": qty, "entryPrice": entry, "positionStatus": "OPEN", "positionId": "p1"}


class FakeStrategy(Strategy):
    name = "fake"
    fee_gate_enabled = False

    def __init__(self):
        super().__init__({"kline_interval": "1h", "stop_loss_pct": 2.0, "take_profit_pct": 6.0})
        self.entry = None   # EntryPlan to offer, or None
        self.exit = None    # (reason, price, is_maker) to offer once a position exists

    def min_candles_needed(self): return 1
    def compute_indicators(self, candles): return {}
    def entry_plan(self, price, candle, indicators): return self.entry
    def exit_decision(self, position, candle, indicators, bars_held): return self.exit
    def status_line(self, price, indicators): return f"price={price}"


class FakeRisk:
    def __init__(self): self.results = []
    def can_open_new_trade(self): return True
    def position_size(self, entry, stop): return 0.5, {}
    def record_trade_result(self, gross, fees): self.results.append((gross, fees))


class FakeTracker:
    def __init__(self): self.trades = []
    def record(self, entry, exit_, qty, reason, gross, fees): self.trades.append((reason, entry, exit_, qty))


CONFIG = {"symbol": SYMBOL, "poll_seconds": 0, "dry_run": False, "order_timeout_seconds": 180,
          "account_capital": 5000, "risk_per_trade_pct": 1,
          "fees": {"maker_fee_pct": 0.016, "taker_fee_pct": 0.04, "gst_pct": 18}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(live_engine.time, "sleep", lambda s: None)


def run_loop(ex, strategy=None, config=None, risk=None, tracker=None):
    strategy = strategy or FakeStrategy()
    risk, tracker = risk or FakeRisk(), tracker or FakeTracker()
    cfg = dict(CONFIG, **(config or {}))
    executor = OrderExecutor(ex, SYMBOL, cfg["dry_run"])
    try:
        live_engine.run(strategy, ex, executor, risk, tracker, cfg)
    except Stop:
        pass
    return strategy, risk, tracker


def stops(ex):
    return [p for p in ex.placed if p["type"] == "STOP_MARKET"]


def test_adopts_open_position_and_places_one_stop():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)          # e.g. bot restarted with a position open
    run_loop(ex)
    assert len(stops(ex)) == 1                      # one stop, not one per loop
    s = stops(ex)[0]
    assert s["side"] == "SELL" and s["reduceOnly"] is True
    assert s["stopPrice"] == 98.0 and s["quantity"] == 0.5   # entry 100, sl 2%


def test_adopted_short_gets_buy_stop_above_entry():
    ex = FakeExchange()
    ex.position = pos("SHORT", 0.5, 100.0)
    run_loop(ex)
    s = stops(ex)[0]
    assert s["side"] == "BUY" and s["stopPrice"] == 102.0


def test_unfilled_limit_entry_is_not_a_position_and_is_cancelled_after_timeout(monkeypatch):
    ex = FakeExchange()
    ex.max_iterations = 3
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    clock = {"t": 1000.0}
    monkeypatch.setattr("shark.live_sync.time.time", lambda: clock["t"])
    ex.on_iteration = lambda f, i: clock.update(t=clock["t"] + 200)   # each loop = 200s > 180s timeout
    _, risk, tracker = run_loop(ex, strat)
    entries = [p for p in ex.placed if p["reduceOnly"] is False]
    assert len(entries) >= 1
    assert ex.cancelled, "stale entry order must be cancelled"
    assert stops(ex) == [] and tracker.trades == [] and risk.results == []   # never became a position


def test_second_entry_not_placed_while_first_is_pending():
    ex = FakeExchange()
    ex.max_iterations = 3
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    run_loop(ex, strat)                              # clock real -> timeout never reached
    assert len([p for p in ex.placed if p["reduceOnly"] is False]) == 1


def test_filled_entry_confirmed_then_stop_placed():
    ex = FakeExchange()
    ex.fill_on_place = True
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    run_loop(ex, strat)
    assert len([p for p in ex.placed if p["reduceOnly"] is False]) == 1
    assert len(stops(ex)) == 1 and stops(ex)[0]["stopPrice"] == 98.0


def test_stop_hit_on_exchange_is_booked_and_orders_cleaned_up():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)
    ex.max_iterations = 4

    def gone(f, i):
        if i == 3:                                    # exchange stop triggered between polls
            f.position = None
    ex.on_iteration = gone
    _, risk, tracker = run_loop(ex)
    assert [t[0] for t in tracker.trades] == ["closed_on_exchange"]
    assert len(risk.results) == 1
    assert ex.orders == []                            # leftover stop cancelled


def test_strategy_exit_cancels_stop_and_books_only_after_exchange_confirms():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)
    ex.max_iterations = 4
    strat = FakeStrategy()

    def trigger(f, i):
        if i == 2:
            strat.exit = ("sar_flip", 101.0, False)   # market exit
    ex.on_iteration = trigger
    _, risk, tracker = run_loop(ex, strat)
    exits = [p for p in ex.placed if p["reduceOnly"] is True and p["type"] == "MARKET"]
    assert len(exits) == 1 and exits[0]["side"] == "SELL"
    assert any(c.startswith("cid") for c in ex.cancelled)          # resting stop cancelled first
    assert [t[0] for t in tracker.trades] == ["sar_flip"]         # booked once, with our reason
    assert len(risk.results) == 1


def test_positions_api_error_means_do_nothing():
    ex = FakeExchange()
    ex.positions_error = RuntimeError("exchange down")
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    _, risk, tracker = run_loop(ex, strat)
    assert ex.placed == [] and tracker.trades == []   # unknown state is never treated as flat


def test_stop_placement_failure_does_not_crash_and_retries():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)
    real_post = ex.signed_post
    calls = {"n": 0}

    def flaky(endpoint, params):
        if params["type"] == "STOP_MARKET":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("stop rejected")
        return real_post(endpoint, params)
    ex.signed_post = flaky
    run_loop(ex)
    assert calls["n"] >= 2 and len(stops(ex)) == 1    # retried next loop, then succeeded


def test_dry_run_makes_no_exchange_calls():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)             # would be adopted in real mode
    ex.signed_get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run touched the exchange"))
    ex.signed_post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run touched the exchange"))
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    run_loop(ex, strat, config={"dry_run": True})


class TestExchangeReaders:
    def test_get_open_position_flat(self):
        ex = FakeExchange()
        assert ex.get_open_position(SYMBOL) is None

    def test_get_open_position_parses_docs_shape_and_wrapper(self):
        ex = FakeExchange()
        ex.position = pos("SHORT", -0.25, 99.5)
        got = ex.get_open_position(SYMBOL)
        assert got == {"side": "SHORT", "qty": 0.25, "entry_price": 99.5, "position_id": "p1"}

    def test_other_symbols_ignored_and_bad_side_raises(self):
        ex = FakeExchange()
        ex.position = dict(pos("LONG", 1, 1), contractPair="BTCUSDT")
        assert ex.get_open_position(SYMBOL) is None
        ex.position = dict(pos("LONG", 1, 1), positionType="WAT")
        with pytest.raises(ValueError):
            ex.get_open_position(SYMBOL)
