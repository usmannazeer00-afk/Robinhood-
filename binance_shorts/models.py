"""Data model for a Binance USDT-M perpetual futures candidate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Base-asset volume traded by takers who bought (Binance kline field 9).
    # volume - taker_buy_base_volume is the taker *sell* side.
    taker_buy_base_volume: float
    close_time: datetime

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def taker_sell_base_volume(self) -> float:
        return max(self.volume - self.taker_buy_base_volume, 0.0)


@dataclass
class FuturesSnapshot:
    """A point-in-time read of one perpetual's market state, as returned
    by a data provider -- everything the scoring engine needs and nothing
    it would need to place or manage a trade."""

    symbol: str
    candles_15m: list[Candle] = field(default_factory=list)
    candles_htf: list[Candle] = field(default_factory=list)

    quote_volume_24h: float = 0.0
    funding_rate: float | None = None  # latest, as a fraction (0.0001 = 0.01%)
    open_interest: float | None = None
    open_interest_prev: float | None = None  # a few periods back, for the OI-change check

    @property
    def last_price(self) -> float:
        return self.candles_15m[-1].close if self.candles_15m else 0.0

    @property
    def open_interest_change_pct(self) -> float | None:
        if not self.open_interest or not self.open_interest_prev:
            return None
        return (self.open_interest - self.open_interest_prev) / self.open_interest_prev * 100
