"""Synthetic provider for demos and tests.

Generates a handful of candidate symbols spanning the range the scorer
cares about (a clean downtrend-pullback short setup, a strong uptrend
that should be avoided, a choppy/no-setup symbol, and a too-new symbol
with insufficient history) so a scan has something to show before real
Binance network access is wired up -- and so the scoring factors have
known-shape inputs to test against.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import Candle, FuturesSnapshot
from .base import FuturesDataProvider


def _candles(
    start: datetime,
    interval_minutes: int,
    closes: list[float],
    volumes: list[float] | None = None,
    taker_buy_ratios: list[float] | None = None,
    wick_pct: float = 0.001,
) -> list[Candle]:
    candles = []
    t = start
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        volume = volumes[i] if volumes else 1_000.0
        buy_ratio = taker_buy_ratios[i] if taker_buy_ratios else 0.5
        high = max(open_, close) * (1 + wick_pct)
        low = min(open_, close) * (1 - wick_pct)
        candles.append(
            Candle(
                open_time=t,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                taker_buy_base_volume=volume * buy_ratio,
                close_time=t + timedelta(minutes=interval_minutes),
            )
        )
        prev_close = close
        t += timedelta(minutes=interval_minutes)
    return candles


def _downtrend_pullback_15m(now: datetime) -> list[Candle]:
    """A staircase of lower highs/lower lows (8 legs, clearly confirmed
    swing points), followed by a sharp 14-bar relief rally back up toward
    the most recent swing-high resistance, ending in a shooting-star
    rejection candle on 2.5x average volume with sell-dominated taker
    flow. The rally is steep enough to push RSI into overbought territory
    right before it rolls over on the final candle -- the textbook
    pullback-short setup this scorer is built to reward."""
    closes: list[float] = []
    peak = 120.0
    for leg in range(8):
        top = peak - leg * 4.0
        bottom = top - 8.0
        closes.extend([top - 1, top, top - 3, bottom + 3, bottom + 1, bottom])

    last_bottom = closes[-1]
    closes.extend(last_bottom + i * 1.7 for i in range(1, 15))

    volumes = [1_000.0] * len(closes)
    taker_buy = [0.5] * len(closes)
    candles = _candles(now - timedelta(minutes=15 * len(closes)), 15, closes, volumes, taker_buy)

    final_open = candles[-1].close
    final = Candle(
        open_time=candles[-1].close_time,
        open=final_open,
        high=final_open * 1.03,
        low=final_open * 0.955,
        close=final_open * 0.965,
        volume=2_500.0,
        taker_buy_base_volume=2_500.0 * 0.2,  # sell-dominated
        close_time=candles[-1].close_time + timedelta(minutes=15),
    )
    candles.append(final)
    return candles


def _downtrend_htf(now: datetime, bars: int = 80, uptick_last_two: bool = False) -> list[Candle]:
    closes = [110.0 - i * 0.2 for i in range(bars - (2 if uptick_last_two else 0))]
    if uptick_last_two:
        # A small end-of-window uptick (the pullback that fuels the 15m
        # rally above) without disturbing the overall downtrend the EMA
        # filter and open-interest-into-the-rally check both look at.
        closes.extend([closes[-1] + 0.5, closes[-1] + 1.0])
    start = now - timedelta(hours=len(closes) - 1)
    return _candles(start, 60, closes)


def _uptrend_htf(now: datetime, bars: int = 80) -> list[Candle]:
    closes = [80.0 + i * 0.3 for i in range(bars)]
    start = now - timedelta(hours=bars - 1)
    return _candles(start, 60, closes)


def _choppy_15m(now: datetime, bars: int = 70) -> list[Candle]:
    closes = [50.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(bars)]
    start = now - timedelta(minutes=15 * (bars - 1))
    return _candles(start, 15, closes)


class MockFuturesProvider(FuturesDataProvider):
    def fetch_candidates(self, config) -> list[FuturesSnapshot]:
        now = datetime.now(timezone.utc)

        return [
            FuturesSnapshot(
                symbol="SHORTMEUSDT",
                candles_15m=_downtrend_pullback_15m(now),
                candles_htf=_downtrend_htf(now, uptick_last_two=True),
                quote_volume_24h=250_000_000,
                funding_rate=0.0006,
                open_interest=120_000,
                open_interest_prev=100_000,
            ),
            FuturesSnapshot(
                symbol="MOONUSDT",
                candles_15m=_choppy_15m(now),
                candles_htf=_uptrend_htf(now),
                quote_volume_24h=180_000_000,
                funding_rate=0.0001,
                open_interest=90_000,
                open_interest_prev=95_000,
            ),
            FuturesSnapshot(
                symbol="CHOPPYUSDT",
                candles_15m=_choppy_15m(now),
                candles_htf=_downtrend_htf(now),
                quote_volume_24h=60_000_000,
                funding_rate=-0.0001,
                open_interest=40_000,
                open_interest_prev=41_000,
            ),
            FuturesSnapshot(
                symbol="TOOTHINUSDT",
                candles_15m=_choppy_15m(now, bars=20),
                candles_htf=_downtrend_htf(now, bars=20),
                quote_volume_24h=5_000_000,
                funding_rate=0.0002,
                open_interest=None,
                open_interest_prev=None,
            ),
        ]
