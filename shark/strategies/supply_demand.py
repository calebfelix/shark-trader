"""Supply/demand zone rejection. A zone is the small "base" (1-3 low-range candles) that an impulse candle
broke away from: a breakout up leaves a demand zone, a breakout down a supply zone. Only FRESH zones count
(price has not come back since the impulse). When price first retraces into one and a candle rejects it
(a wick into the zone, a close back out), enter with the zone's far edge as the stop and a risk multiple as
the target. Decides on closed candles only."""

from typing import Optional

from .base import EntryPlan, Position, Strategy


class SupplyDemandStrategy(Strategy):
    name = "supply_demand"
    fee_gate_enabled = True
    acts_on_closed_candles = True

    def min_candles_needed(self) -> int:
        p = self.params
        trend = p["trend_period"] if p.get("trend_filter") else 0
        return max(p["atr_period"] + p["max_age"] + 5, trend + 1)

    def candles_to_fetch(self) -> int:
        return self.min_candles_needed() + int(self.params["max_bars"]) + 10

    def recovery_levels(self, entry: float, side: str) -> tuple[float, float]:
        sgn = 1 if side == "LONG" else -1
        pct = self.params["fallback_stop_pct"] / 100.0
        return entry * (1 - sgn * pct), entry * (1 + sgn * pct * self.params["rr"])

    @staticmethod
    def _true_ranges(w: list[dict]) -> list[float]:
        tr = [w[0]["high"] - w[0]["low"]]
        for i in range(1, len(w)):
            tr.append(max(w[i]["high"] - w[i]["low"], abs(w[i]["high"] - w[i - 1]["close"]),
                          abs(w[i]["low"] - w[i - 1]["close"])))
        return tr

    def compute_indicators(self, candles: list[dict]) -> Optional[dict]:
        p = self.params
        if len(candles) < self.min_candles_needed():
            return None
        w = candles[-(p["atr_period"] + p["max_age"] + 5):]
        n, per = len(w), p["atr_period"]
        tr = self._true_ranges(w)
        atr_now = sum(tr[-per:]) / per
        last = w[-1]
        found = []          # (impulse index, side, proximal, distal)
        for i in range(per + 1, n - 1):
            a = sum(tr[i - per:i]) / per                          # ATR just BEFORE the impulse
            c = w[i]
            if a <= 0 or abs(c["close"] - c["open"]) < p["imp_mult"] * a:
                continue
            up = c["close"] > c["open"]
            base = []
            for j in range(i - 1, max(i - 1 - p["max_base"], per - 1), -1):
                if abs(w[j]["close"] - w[j]["open"]) > p["base_body_frac"] * a:
                    break
                base.append(w[j])
            if not base:
                continue
            if up:
                if c["close"] <= max(b["high"] for b in base):
                    continue
                prox, distal = max(max(b["open"], b["close"]) for b in base), min(b["low"] for b in base)
            else:
                if c["close"] >= min(b["low"] for b in base):
                    continue
                prox, distal = min(min(b["open"], b["close"]) for b in base), max(b["high"] for b in base)
            if not (0.05 * a <= abs(prox - distal) <= p["max_zone_atr"] * a):
                continue
            fresh = True
            for k in range(i + 1, n - 1):                        # candles between the impulse and now
                x = w[k]
                if (x["low"] <= prox or x["close"] < distal) if up else (x["high"] >= prox or x["close"] > distal):
                    fresh = False
                    break
            if fresh:
                found.append((i, "LONG" if up else "SHORT", prox, distal))

        signal = None
        rng = last["high"] - last["low"]
        for i, side, prox, distal in sorted(found, reverse=True):   # most recent zone first
            if side == "LONG" and last["low"] <= prox and rng > 0:
                lower_wick = min(last["open"], last["close"]) - last["low"]
                if last["close"] > prox and lower_wick >= p["wick_frac"] * rng:
                    stop = min(last["low"], distal) - p["stop_buffer_atr"] * atr_now
                    signal = {"side": "LONG", "stop": stop, "zone": (distal, prox)}
                    break
            elif side == "SHORT" and last["high"] >= prox and rng > 0:
                upper_wick = last["high"] - max(last["open"], last["close"])
                if last["close"] < prox and upper_wick >= p["wick_frac"] * rng:
                    stop = max(last["high"], distal) + p["stop_buffer_atr"] * atr_now
                    signal = {"side": "SHORT", "stop": stop, "zone": (prox, distal)}
                    break
        if signal and p.get("trend_filter"):
            sma = sum(x["close"] for x in candles[-p["trend_period"]:]) / p["trend_period"]
            if (signal["side"] == "LONG") != (last["close"] > sma):
                signal = None
        live = [z for z in found if (last["low"] > z[2] if z[1] == "LONG" else last["high"] < z[2])]
        return {"atr": atr_now, "signal": signal, "zones": len(live)}

    def entry_plan(self, price: float, candle: dict, indicators: Optional[dict]) -> Optional[EntryPlan]:
        if indicators is None or indicators["signal"] is None:
            return None
        p, sig = self.params, indicators["signal"]
        risk = abs(price - sig["stop"])
        if not (p["min_stop_atr"] * indicators["atr"] <= risk <= p["max_stop_atr"] * indicators["atr"]):
            return None                                           # stop too tight to size sanely, or too wide
        sgn = 1 if sig["side"] == "LONG" else -1
        return EntryPlan(entry_price=price, stop_loss_price=sig["stop"],
                         take_profit_price=price + sgn * p["rr"] * risk, is_maker=False, side=sig["side"])

    def exit_decision(self, position: Position, candle: dict, indicators: Optional[dict],
                      bars_held: int) -> Optional[tuple[str, float, bool]]:
        long = position.side == "LONG"
        if (candle["low"] <= position.stop_loss_price) if long else (candle["high"] >= position.stop_loss_price):
            return "stop_loss", position.stop_loss_price, False
        if (candle["high"] >= position.take_profit_price) if long else (candle["low"] <= position.take_profit_price):
            return "take_profit", position.take_profit_price, True
        if bars_held >= self.params["max_bars"]:
            return "time_stop", candle["close"], False
        return None

    def status_line(self, price: float, indicators: Optional[dict]) -> str:
        if indicators is None:
            return f"price={price:.2f} - warming up"
        sig = indicators["signal"]
        return (f"price={price:.2f} fresh zones={indicators['zones']} ATR={indicators['atr']:.3f} "
                f"{'SIGNAL ' + sig['side'] if sig else 'waiting for a rejection'}")
