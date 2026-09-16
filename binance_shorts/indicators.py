"""Pure-Python technical indicators -- no numpy/pandas dependency, matching
the rest of this repo. Every series function returns a list the same
length as its input, front-padded with ``None`` wherever there isn't yet
enough history to compute a value, so callers can always index by the
same position as the source candles/closes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Candle


def ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)

    multiplier = 2 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    prev = seed
    for v in values[period:]:
        prev = (v - prev) * multiplier + prev
        result.append(prev)
    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    n = len(values)
    if n <= period:
        return [None] * n

    result: list[float | None] = [None] * period
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    result.append(_rsi_from_averages(avg_gain, avg_loss))

    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(_rsi_from_averages(avg_gain, avg_loss))
    return result


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = [
        f - s if f is not None and s is not None else None for f, s in zip(ema_fast, ema_slow)
    ]

    valid = [m for m in macd_line if m is not None]
    signal_valid = ema(valid, signal)
    pad = len(macd_line) - len(valid)
    signal_line: list[float | None] = [None] * pad + signal_valid

    histogram: list[float | None] = [
        m - s if m is not None and s is not None else None for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def bollinger_bands(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(values)
    upper: list[float | None] = [None] * n
    mid: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n

    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        m = sum(window) / period
        variance = sum((x - m) ** 2 for x in window) / period
        sd = variance**0.5
        mid[i] = m
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return upper, mid, lower


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    n = len(candles)
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.high - c.low)
        else:
            prev_close = candles[i - 1].close
            trs.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))

    result: list[float | None] = [None] * n
    if n < period:
        return result

    seed = sum(trs[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        result[i] = prev
    return result


@dataclass
class SwingPoint:
    index: int
    price: float


def swing_highs(candles: list[Candle], left: int = 2, right: int = 2) -> list[SwingPoint]:
    highs = [c.high for c in candles]
    points = []
    for i in range(left, len(highs) - right):
        if all(highs[i] > highs[i - j] for j in range(1, left + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, right + 1)
        ):
            points.append(SwingPoint(i, highs[i]))
    return points


def swing_lows(candles: list[Candle], left: int = 2, right: int = 2) -> list[SwingPoint]:
    lows = [c.low for c in candles]
    points = []
    for i in range(left, len(lows) - right):
        if all(lows[i] < lows[i - j] for j in range(1, left + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, right + 1)
        ):
            points.append(SwingPoint(i, lows[i]))
    return points


def is_bearish_engulfing(prev: Candle, curr: Candle) -> bool:
    return (
        prev.close > prev.open
        and curr.close < curr.open
        and curr.open >= prev.close
        and curr.close <= prev.open
    )


def is_shooting_star(candle: Candle) -> bool:
    body = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.close, candle.open)
    lower_wick = min(candle.close, candle.open) - candle.low
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    return upper_wick >= 2 * body and lower_wick <= body * 0.5 and body / candle_range < 0.4


def bearish_rsi_divergence(
    closes: list[float], rsi_values: list[float | None], highs: list[SwingPoint]
) -> bool:
    """Price making a higher high while RSI makes a lower high at the
    corresponding swing points -- momentum fading even as price extends."""
    if len(highs) < 2:
        return False
    prev, last = highs[-2], highs[-1]
    r_prev, r_last = rsi_values[prev.index], rsi_values[last.index]
    if r_prev is None or r_last is None:
        return False
    return last.price > prev.price and r_last < r_prev
