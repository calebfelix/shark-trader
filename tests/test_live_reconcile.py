"""Real-account live loop: the exchange's position is the source of truth.
Drives the real live_engine.run() against a fake exchange - no network."""

import pytest

from shark import live_engine
from shark.bot_registry import BotRegistry
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
        self.trade_history = []       # rows in Shark's trade-history shape
        self.ledger = []              # rows in Shark's transaction-history shape

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
        if endpoint == "/v1/user-data/trade-history":
            return list(self.trade_history)
        if endpoint == "/v1/user-data/transaction-history":
            return list(self.ledger)
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
            self.trade_history.append({"id": 1000 + self._n, "positionId": "p1", "clientOrderId": cid,
                                       "side": params["side"], "quantity": params["quantity"], "price": 100.0,
                                       "time": "2026-10-20T13:58:56.927Z"})
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
            "quantity": qty, "entryPrice": entry, "positionStatus": "OPEN", "positionId": "p1",
            "createdAt": "2026-10-20T13:58:56.942Z"}


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
    def __init__(self): self.trades = []; self.rows = []
    def record(self, entry, exit_, qty, reason, gross, fees, discount=0.0, funding=0.0, extra=None):
        self.trades.append((reason, entry, exit_, qty))
        self.rows.append({"gross": gross, "fees": fees, "discount": discount, "funding": funding, **(extra or {})})


CONFIG = {"symbol": SYMBOL, "poll_seconds": 0, "dry_run": False, "order_timeout_seconds": 180,
          "account_capital": 5000, "risk_per_trade_pct": 1,
          "fees": {"maker_fee_pct": 0.016, "taker_fee_pct": 0.04, "gst_pct": 18}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(live_engine.time, "sleep", lambda s: None)


def run_loop(ex, strategy=None, config=None, risk=None, tracker=None, quote_to_inr_rate=1.0,
             registry=None, own_p1=True):
    strategy = strategy or FakeStrategy()
    risk, tracker = risk or FakeRisk(), tracker or FakeTracker()
    cfg = dict(CONFIG, **(config or {}))
    registry = registry or BotRegistry(None)
    if own_p1:
        registry.add_position("p1")          # a position this bot opened earlier (e.g. before a restart)
    executor = OrderExecutor(ex, SYMBOL, cfg["dry_run"], registry=registry)
    try:
        live_engine.run(strategy, ex, executor, risk, tracker, cfg, quote_to_inr_rate=quote_to_inr_rate)
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
    assert [t[0] for t in tracker.trades] == ["closed_on_exchange_estimated"]   # no history -> estimate, flagged
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
    assert [t[0] for t in tracker.trades] == ["sar_flip_estimated"]   # booked once, with our reason
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
        assert {k: got[k] for k in ("side", "qty", "entry_price", "position_id", "opened_at_ms")} == \
            {"side": "SHORT", "qty": 0.25, "entry_price": 99.5, "position_id": "p1", "opened_at_ms": 1792504736942}

    def test_other_symbols_ignored_and_bad_side_raises(self):
        ex = FakeExchange()
        ex.position = dict(pos("LONG", 1, 1), contractPair="BTCUSDT")
        assert ex.get_open_position(SYMBOL) is None
        ex.position = dict(pos("LONG", 1, 1), positionType="WAT")
        with pytest.raises(ValueError):
            ex.get_open_position(SYMBOL)


# ---- settlement from Shark's own records (shapes copied from real responses, 2026-10-20) ----

def _trade(id_, time_, side, price, qty, fee_inr, realized_inr=None, role="TAKER", position_id="p1"):
    return {"id": id_, "time": time_, "symbol": SYMBOL, "type": "LIMIT", "side": side, "price": price,
            "quantity": qty, "role": role, "fee": 0.0, "feeInMarginAsset": fee_inr,
            "realizedProfit": 0, "realizedProfitInMarginAsset": realized_inr, "positionId": position_id}


def _ledger(time_, type_, amount, trade_id=None):
    return {"id": 1, "time": time_, "type": type_, "amount": amount, "asset": "INR",
            "symbol": SYMBOL, "tradeId": trade_id}


def settled_exchange():
    """LONG 0.24 opened 13:58:56.9, closed next day 09:00. Real shapes, incl. discount rows that have no tradeId."""
    ex = FakeExchange()
    ex.trade_history = [
        _trade(1, "2026-10-20T13:58:56.927Z", "BUY", 105.07, 0.24, 1.22),
        _trade(2, "2026-10-21T09:00:00.000Z", "SELL", 107.00, 0.24, 1.30, realized_inr=47.26),
        _trade(9, "2026-10-21T09:00:00.000Z", "SELL", 1.0, 1.0, 99.0, realized_inr=999.0, position_id="other"),
    ]
    ex.ledger = [
        _ledger("2026-10-20T13:58:56.927Z", "COMMISSION", -1.03, 1),
        _ledger("2026-10-20T13:58:57.000Z", "FEE_DISCOUNT", 0.12),          # follows trade 1 by ~70ms
        _ledger("2026-10-21T00:00:00.700Z", "FUNDING_FEE", -0.50),
        _ledger("2026-10-21T00:00:00.700Z", "GST_ON_FUNDING_FEE", -0.09),
        _ledger("2026-10-21T09:00:00.070Z", "FEE_DISCOUNT", 0.13),          # follows trade 2
        _ledger("2026-10-22T00:00:00.700Z", "FUNDING_FEE", -7.00),          # after close: not ours
        _ledger("2026-10-19T00:00:00.700Z", "FUNDING_FEE", -7.00),          # before open: not ours
        _ledger("2026-10-20T20:00:00.000Z", "FEE_DISCOUNT", 5.00),          # unrelated discount: not ours
    ]
    return ex


def test_settle_position_adds_up_fees_discounts_and_funding():
    from shark.settlement import settle_position
    s = settle_position(settled_exchange(), SYMBOL, "p1", "LONG", 1792504736942)
    assert s["realized_inr"] == pytest.approx(47.26)
    assert s["fees_inr"] == pytest.approx(2.52)               # 1.22 + 1.30 (GST already included)
    assert s["discounts_inr"] == pytest.approx(0.25)          # only the two discounts that follow our fills
    assert s["funding_inr"] == pytest.approx(-0.59)           # only funding while the position was open
    assert s["net_inr"] == pytest.approx(47.26 - 2.52 + 0.25 - 0.59)
    assert (s["entry_price"], s["exit_price"], s["qty"]) == pytest.approx((105.07, 107.0, 0.24))


def test_settle_position_returns_none_until_the_close_is_complete():
    from shark.settlement import settle_position
    ex = settled_exchange()
    ex.trade_history = ex.trade_history[:1]                   # exit fill not visible yet
    assert settle_position(ex, SYMBOL, "p1", "LONG", 1792504736942, retries=2, wait_seconds=0) is None
    ex.trade_history = [ex.trade_history[0], dict(_trade(2, "2026-10-21T09:00:00.000Z", "SELL", 107.0, 0.10, 1.0))]
    assert settle_position(ex, SYMBOL, "p1", "LONG", 1792504736942, retries=1, wait_seconds=0) is None  # partial close


def test_engine_books_the_exchanges_real_numbers_in_inr():
    ex = settled_exchange()
    ex.position = pos("LONG", 0.24, 105.07)
    ex.max_iterations = 4
    ex.on_iteration = lambda f, i: setattr(f, "position", None) if i == 3 else None
    _, risk, tracker = run_loop(ex, quote_to_inr_rate=96.0)
    assert len(tracker.trades) == 1
    reason, entry, exit_, qty = tracker.trades[0]
    assert reason == "closed_on_exchange"                      # not "_estimated"
    assert (entry, exit_, qty) == pytest.approx((105.07, 107.0, 0.24))
    realized, costs = risk.results[0]
    assert realized == pytest.approx(47.26)                    # INR straight from Shark, NOT scaled again
    assert costs == pytest.approx(2.52 - 0.25 + 0.59)          # fees - discounts - funding(signed)
    row = tracker.rows[0]                                       # each cost lands in its own column
    assert (row["fees"], row["discount"], row["funding"]) == pytest.approx((2.52, 0.25, -0.59))
    assert (row["source"], row["side"], row["entry_role"], row["position_id"]) == ("settled", "LONG", "TAKER", "p1")


def test_estimate_fallback_is_converted_to_inr():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 90.0)                       # candles close at 100 -> +10 USDT/SOL
    ex.max_iterations = 4
    ex.on_iteration = lambda f, i: setattr(f, "position", None) if i == 3 else None
    _, risk, tracker = run_loop(ex, quote_to_inr_rate=100.0)
    assert tracker.trades[0][0] == "closed_on_exchange_estimated"
    gross, fees = risk.results[0]
    assert gross == pytest.approx((100.0 - 90.0) * 0.5 * 100.0)          # 5 USDT -> 500 INR
    assert fees == pytest.approx(0.5 * 90.0 * (0.016 * 1.18 + 0.04 * 1.18) / 100 * 100.0)


def test_dry_run_pnl_is_converted_to_inr_for_the_risk_limits():
    ex = FakeExchange()
    ex.max_iterations = 4
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")

    def step(f, i):
        if i == 2:
            strat.entry = None
            strat.exit = ("take_profit", 106.0, True)
    ex.on_iteration = step
    _, risk, tracker = run_loop(ex, strat, config={"dry_run": True}, quote_to_inr_rate=96.0)
    gross, _fees = risk.results[0]
    assert gross == pytest.approx((106.0 - 100.0) * 0.5 * 96.0)          # 3 USDT -> 288 INR, so the 3% daily-loss limit is in INR


# ---- isolation: the owner's manual trades are never touched ----

def test_foreign_position_is_left_completely_alone():
    ex = FakeExchange()
    ex.position = pos("LONG", 3.0, 99.0)                  # opened by hand: no bot order behind it
    ex.orders = [{"clientOrderId": "manual-stop", "symbol": SYMBOL, "type": "STOP_MARKET", "stopPrice": 90.0}]
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    strat.exit = ("sar_flip", 101.0, False)               # would close a position if it thought it owned one
    _, risk, tracker = run_loop(ex, strat, own_p1=False)
    assert ex.placed == []                                  # no stop, no entry, no exit
    assert ex.cancelled == []                               # the manual stop order survives
    assert ex.orders[0]["clientOrderId"] == "manual-stop"
    assert tracker.trades == [] and risk.results == []      # never booked as a bot trade


def test_position_is_adopted_when_its_fill_came_from_a_bot_order():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)
    ex.trade_history = [{"id": 1, "positionId": "p1", "clientOrderId": "bot-entry-1", "side": "BUY",
                         "quantity": 0.5, "price": 100.0, "time": "2026-10-20T13:58:56.927Z"}]
    reg = BotRegistry(None)
    reg.add_order("bot-entry-1")                            # we placed it (e.g. before a crash)
    run_loop(ex, registry=reg, own_p1=False)
    assert len(stops(ex)) == 1 and reg.owns_position("p1")


def test_only_our_stop_is_recognised_so_a_manual_stop_does_not_count_as_protection():
    ex = FakeExchange()
    ex.position = pos("LONG", 0.5, 100.0)
    ex.orders = [{"clientOrderId": "manual-stop", "symbol": SYMBOL, "type": "STOP_MARKET", "stopPrice": 50.0}]
    run_loop(ex)
    assert len(stops(ex)) == 1                              # we still placed OUR stop
    assert any(o["clientOrderId"] == "manual-stop" for o in ex.orders)   # and left theirs


def test_no_new_entries_while_a_foreign_position_holds_the_symbol():
    ex = FakeExchange()
    ex.position = pos("SHORT", 1.0, 100.0)
    strat = FakeStrategy()
    strat.entry = EntryPlan(100.0, 98.0, 106.0, True, "LONG")
    run_loop(ex, strat, own_p1=False)
    assert [p for p in ex.placed if p["reduceOnly"] is False] == []


def test_registry_persists_across_restarts(tmp_path):
    path = str(tmp_path / "state.json")
    r = BotRegistry(path)
    r.add_order("o1"); r.add_position("p9")
    r2 = BotRegistry(path)
    assert r2.owns_order("o1") and r2.owns_position("p9") and not r2.owns_order("other")
    assert not BotRegistry(str(tmp_path / "missing.json")).owns_order("o1")
    (tmp_path / "bad.json").write_text("{not json")
    assert not BotRegistry(str(tmp_path / "bad.json")).owns_order("o1")   # unreadable -> trust nothing


def test_executor_registers_every_order_it_places():
    ex = FakeExchange()
    reg = BotRegistry(None)
    exe = OrderExecutor(ex, SYMBOL, False, registry=reg)
    r1 = exe.place_limit_order("BUY", 0.5, 99.0, reduce_only=False)
    r2 = exe.place_stop_market("SELL", 0.5, 95.0)
    assert reg.owns_order(r1["clientOrderId"]) and reg.owns_order(r2["clientOrderId"])


# ---- the trade log carries every cost ----

def test_tracker_splits_fees_discount_and_funding_into_columns(tmp_path):
    import csv
    from shark.performance import PerformanceTracker
    path = str(tmp_path / "t.csv")
    t = PerformanceTracker(path, 5000.0, SYMBOL)
    t.record(105.07, 107.0, 0.24, "sar_flip", 47.26, 2.52, discount=0.25, funding=-0.59,
             extra={"side": "LONG", "source": "settled", "entry_role": "TAKER", "exit_role": "TAKER",
                    "position_id": "p1"})
    row = list(csv.DictReader(open(path)))[0]
    assert float(row["net_pnl"]) == pytest.approx(47.26 - 2.52 + 0.25 - 0.59)
    assert (row["fees"], row["fee_discount"], row["funding"]) == ("2.52", "0.25", "-0.59")
    assert row["source"] == "settled" and row["side"] == "LONG" and row["position_id"] == "p1"
    assert t.equity == pytest.approx(5000 + 44.40)


def test_tracker_upgrades_an_older_csv_without_losing_rows(tmp_path):
    import csv
    from shark.performance import PerformanceTracker
    path = tmp_path / "old.csv"
    path.write_text("timestamp,symbol,entry_price,exit_price,qty,exit_reason,gross_pnl,fees,net_pnl,equity_after\n"
                    "2026-10-01T10:00:00,SOLUSDT,100,101,1,tp,1.0,0.1,0.9,5000.9\n")
    PerformanceTracker(str(path), 5000.0, SYMBOL)
    rows = list(csv.DictReader(open(path)))
    assert PerformanceTracker.COLUMNS == list(rows[0].keys())
    assert rows[0]["exit_reason"] == "tp" and rows[0]["net_pnl"] == "0.9" and rows[0]["fee_discount"] == ""
