"""Candle-interval arithmetic shared by the live engine."""

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}


def bars_held(closed_candles: list[dict], opened_at_ms: int | None, interval_ms: int) -> int:
    """Candles that have CLOSED since the position opened - the unit the backtest's `bars_held` uses
    (1 at the first candle close after entry), independent of how often the bot polls or restarts."""
    if not opened_at_ms:
        return 0
    return sum(1 for c in closed_candles if c.get("time") is not None and c["time"] + interval_ms > opened_at_ms)
