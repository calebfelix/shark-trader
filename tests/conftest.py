"""Shared fixtures. No test here ever hits the live Shark Exchange API -
exchange.py's signing/parsing logic is tested directly, network calls are
never made."""

import pytest


def make_candles(closes: list[float], high_pad: float = 0.5, low_pad: float = 0.5) -> list[dict]:
    """Builds a minimal oldest-first candle list from a list of closes.
    high/low are the close padded by a small fixed amount so ATR/BB math
    has something non-degenerate to work with; open is the prior close."""
    candles = []
    prev_close = closes[0]
    for c in closes:
        candles.append({
            "open": prev_close,
            "high": c + high_pad,
            "low": c - low_pad,
            "close": c,
            "volume": 100.0,
        })
        prev_close = c
    return candles


@pytest.fixture
def base_config():
    """Minimal config.json-shaped dict, enough for backtest_engine.run_backtest."""
    return {
        "account_capital": 2000.0,
        "risk_per_trade_pct": 1.0,
        "fees": {"maker_fee_pct": 0.016, "taker_fee_pct": 0.040, "gst_pct": 18.0},
    }
