"""Pure indicator functions shared by every strategy and the backtester."""


def bollinger_bands(closes: list[float], period: int, stddev_mult: float):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = mid + stddev_mult * std
    lower = mid - stddev_mult * std
    return upper, mid, lower


def rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles: list[dict], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / period


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period
