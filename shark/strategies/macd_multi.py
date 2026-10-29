"""Structured multi-entry MACD (after tradingrush.net's "87% win rate" test). DRY RUN ONLY - the live engine
holds one position with one target, this needs up to three entries with their own targets and a shared stop,
so it runs on shark/multi_leg_engine.py, which never places a real order.

Rules: on 30m closed candles, a MACD cross up BELOW the zero line while price is above the 4h EMA200 is a long
signal (cross down above zero, below the EMA, a short). The setup's stop is the EMA200 at the first entry, shared
by every entry. Up to `max_entries` entries per setup, each only if it improves the average (lower for longs)
by at least `min_gap_frac` of the stop distance. Each entry targets tp_r[n] x its own stop distance (0.25R,
0.25R, 0.5R by default). Setups whose stop is closer than `min_stop_pct` are skipped: with targets this small,
fees would eat a tight setup (see the backtest notes in CLAUDE.md / the session summary).
"""

from typing import Optional

from .base import Strategy

H4_MS = 14_400_000
M30_MS = 1_800_000


def ema_series(values: list[float], period: int) -> list[float]:
    k, out, e = 2 / (period + 1), [], None
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


class MultiEntryBook:
    """The open entries ("legs") of the current setup. Pure bookkeeping - no I/O, no exchange."""

    def __init__(self, params: dict, slippage_pct: float):
        self.p = params
        self.slip = slippage_pct / 100.0
        self.legs: list[dict] = []
        self.setup_side: Optional[str] = None
        self.setup_stop: Optional[float] = None
        self.last_ref: Optional[float] = None
        self.setups = 0
        self.taken = 0            # entries taken in the current setup (closed ones included)

    def exits(self, candle: dict) -> list[tuple[dict, str, float, bool]]:
        """Legs closed by this candle: (leg, reason, exit price, is_maker). The stop is checked before the target
        when both are inside one candle (the order is unknowable, so assume the worse)."""
        closed, still = [], []
        for leg in self.legs:
            long = leg["side"] == "LONG"
            if (candle["low"] <= leg["stop"]) if long else (candle["high"] >= leg["stop"]):
                px = min(leg["stop"], candle["open"]) if long else max(leg["stop"], candle["open"])   # gaps fill worse
                closed.append((leg, "stop_loss", px * (1 - self.slip if long else 1 + self.slip), False))
            elif (candle["high"] >= leg["tp"]) if long else (candle["low"] <= leg["tp"]):
                closed.append((leg, "take_profit", leg["tp"], True))
            else:
                still.append(leg)
        self.legs = still
        if not self.legs:
            self.setup_side = self.setup_stop = self.last_ref = None
            self.taken = 0
        return closed

    def signal(self, prev_macd: float, prev_sig: float, macd: float, sig: float, close: float,
               ema: float) -> Optional[str]:
        if prev_macd <= prev_sig and macd > sig and macd < 0 and close > ema:
            return "LONG"
        if prev_macd >= prev_sig and macd < sig and macd > 0 and close < ema:
            return "SHORT"
        return None

    def plan_entry(self, side: str, close: float, ema: float) -> tuple[Optional[dict], str]:
        """The next leg for this signal, or (None, why not). Does not change state - call add() once sized."""
        p, long = self.p, side == "LONG"
        if not self.legs:
            stop = ema
            if abs(close - stop) / close * 100 < p["min_stop_pct"]:
                return None, f"stop at EMA200 only {abs(close - stop) / close * 100:.2f}% away (< {p['min_stop_pct']}%)"
            n = 1
        else:
            stop = self.setup_stop
            if side != self.setup_side:
                return None, "opposite to the open setup"
            if self.taken >= p["max_entries"]:
                return None, "setup already has its entries"
            if (close >= self.last_ref) if long else (close <= self.last_ref):
                return None, "would not improve the average entry"
            if abs(close - self.last_ref) < p["min_gap_frac"] * abs(self.last_ref - stop):
                return None, "too close to the previous entry"
            if (close <= stop) if long else (close >= stop):
                return None, "price is beyond the setup stop"
            n = self.taken + 1
        risk = abs(close - stop)
        mult = p["tp_r"][min(n, len(p["tp_r"])) - 1]
        sgn = 1 if long else -1
        return {"side": side, "n": n, "ref": close, "entry": close * (1 + sgn * self.slip), "stop": stop,
                "tp": close + sgn * mult * risk}, ""

    def add(self, leg: dict) -> None:
        if not self.legs:
            self.setups += 1
            self.setup_side, self.setup_stop = leg["side"], leg["stop"]
        self.taken += 1
        leg["setup"] = self.setups
        self.legs.append(leg)
        self.last_ref = leg["ref"]

    def aggregate(self) -> Optional[dict]:
        if not self.legs:
            return None
        qty = sum(leg["qty"] for leg in self.legs)
        return {"side": self.setup_side, "qty": qty, "stop": self.setup_stop, "legs": len(self.legs),
                "entry": sum(leg["entry"] * leg["qty"] for leg in self.legs) / qty,
                "tp": (min if self.setup_side == "LONG" else max)(leg["tp"] for leg in self.legs)}


class MacdMultiStrategy(Strategy):
    name = "macd_multi"
    multi_leg = True          # dry-run only: runs on multi_leg_engine, never on the real-order live engine

    def min_candles_needed(self) -> int:
        return self.params["macd_slow"] + self.params["macd_signal"] + 2

    def series(self, closed30: list[dict], closed4h: list[dict]) -> list[Optional[tuple[float, float, float]]]:
        """(macd, signal, EMA200 of the 4h closes known when that 30m candle closed) per closed 30m candle."""
        p = self.params
        closes = [c["close"] for c in closed30]
        macd = [a - b for a, b in zip(ema_series(closes, p["macd_fast"]), ema_series(closes, p["macd_slow"]))]
        sig = ema_series(macd, p["macd_signal"])
        ema4 = ema_series([c["close"] for c in closed4h], p["ema_period"])
        known = [(c["time"] + H4_MS, e) for c, e in zip(closed4h, ema4)][p["ema_period"]:]   # skip the warm-up
        out, j, cur = [], 0, None
        for c in closed30:
            t = c["time"] + M30_MS
            while j < len(known) and known[j][0] <= t:
                cur = known[j][1]
                j += 1
            out.append((macd[len(out)], sig[len(out)], cur) if cur is not None else None)
        return out

    # The single-position engine interface is not used by this strategy.
    def compute_indicators(self, candles):
        return None

    def entry_plan(self, price, candle, indicators):
        return None

    def exit_decision(self, position, candle, indicators, bars_held):
        return None

    def status_line(self, price, indicators):
        return f"price={price:.2f}"
