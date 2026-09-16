from datetime import datetime, timedelta, timezone

from binance_shorts.indicators import (
    atr,
    bearish_rsi_divergence,
    bollinger_bands,
    ema,
    is_bearish_engulfing,
    is_shooting_star,
    macd,
    rsi,
    swing_highs,
    swing_lows,
)
from binance_shorts.models import Candle


def _candle(open_, high, low, close, i=0):
    t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)
    return Candle(open_time=t, open=open_, high=high, low=low, close=close, volume=1000.0, taker_buy_base_volume=500.0, close_time=t)


def test_ema_seeds_with_simple_average_then_smooths():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = ema(values, 3)
    assert result[:2] == [None, None]
    assert result[2] == sum(values[:3]) / 3
    assert result[-1] > result[2]


def test_ema_returns_all_none_when_not_enough_data():
    assert ema([1, 2], 5) == [None, None]


def test_rsi_is_100_when_every_change_is_a_gain():
    values = list(range(1, 20))  # strictly increasing
    result = rsi(values, 14)
    assert result[14] == 100.0


def test_rsi_is_0_when_every_change_is_a_loss():
    values = list(range(20, 1, -1))  # strictly decreasing
    result = rsi(values, 14)
    assert result[14] == 0.0


def test_macd_line_is_difference_of_fast_and_slow_ema():
    values = [float(i) for i in range(1, 40)]
    macd_line, signal_line, hist = macd(values, fast=5, slow=10, signal=3)
    idx = 15
    ema_fast = ema(values, 5)[idx]
    ema_slow = ema(values, 10)[idx]
    assert macd_line[idx] == ema_fast - ema_slow
    assert hist[idx] == macd_line[idx] - signal_line[idx]


def test_bollinger_bands_upper_and_lower_straddle_the_mean():
    values = [10, 12, 9, 11, 13, 10, 9, 12, 14, 11, 10, 13, 9, 12, 11, 10, 14, 9, 12, 13]
    upper, mid, lower = bollinger_bands(values, period=20, num_std=2.0)
    assert upper[19] > mid[19] > lower[19]


def test_atr_seeds_with_average_true_range_then_smooths():
    candles = [_candle(10 + i, 10 + i + 1, 10 + i - 1, 10 + i, i) for i in range(20)]
    result = atr(candles, period=14)
    assert result[13] is not None
    assert all(v is None for v in result[:13])


def test_swing_highs_finds_local_fractal_peaks():
    highs = [1, 2, 5, 2, 1, 1, 2, 6, 2, 1]
    candles = [_candle(h, h, h, h, i) for i, h in enumerate(highs)]
    points = swing_highs(candles, left=2, right=2)
    assert [p.index for p in points] == [2, 7]


def test_swing_lows_finds_local_fractal_troughs():
    lows = [5, 4, 1, 4, 5, 5, 4, 0, 4, 5]
    candles = [_candle(l, l, l, l, i) for i, l in enumerate(lows)]
    points = swing_lows(candles, left=2, right=2)
    assert [p.index for p in points] == [2, 7]


def test_bearish_engulfing_detects_red_candle_swallowing_prior_green():
    prev = _candle(open_=10, high=12, low=9.5, close=11.5, i=0)  # green
    curr = _candle(open_=12, high=12.2, low=9, close=9.5, i=1)  # red, engulfs prev body
    assert is_bearish_engulfing(prev, curr)


def test_bearish_engulfing_rejects_a_green_candle():
    prev = _candle(open_=10, high=12, low=9.5, close=11.5, i=0)
    curr = _candle(open_=11, high=13, low=10.8, close=12.8, i=1)  # still green
    assert not is_bearish_engulfing(prev, curr)


def test_shooting_star_needs_long_upper_wick_and_small_body_near_the_low():
    candle = _candle(open_=100, high=110, low=99.6, close=99.7)
    assert is_shooting_star(candle)


def test_shooting_star_rejects_a_wide_body_candle():
    candle = _candle(open_=100, high=101, low=90, close=91)
    assert not is_shooting_star(candle)


def test_bearish_rsi_divergence_true_when_price_higher_high_but_rsi_lower_high():
    from binance_shorts.indicators import SwingPoint

    closes = [0.0] * 20
    rsi_values = [None] * 20
    highs = [SwingPoint(5, 100.0), SwingPoint(15, 110.0)]
    rsi_values[5] = 80.0
    rsi_values[15] = 70.0
    assert bearish_rsi_divergence(closes, rsi_values, highs)


def test_bearish_rsi_divergence_false_when_rsi_also_makes_a_higher_high():
    from binance_shorts.indicators import SwingPoint

    closes = [0.0] * 20
    rsi_values = [None] * 20
    highs = [SwingPoint(5, 100.0), SwingPoint(15, 110.0)]
    rsi_values[5] = 60.0
    rsi_values[15] = 75.0
    assert not bearish_rsi_divergence(closes, rsi_values, highs)
