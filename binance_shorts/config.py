"""Tunable parameters for the Binance 15m futures short-setup scanner.

Defaults favor a *pullback-into-resistance* short in a confirmed
downtrend over blind top-picking: a higher-timeframe trend filter keeps
it from fighting a strong uptrend, and the 100-point rubric only pays
out in full when trend, structure, a price-action rejection, momentum,
order flow, and crowded-long positioning all line up.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ShortScoreWeights:
    htf_trend: int = 15
    ltf_structure: int = 15
    resistance_rejection: int = 15
    momentum: int = 15
    volume_orderflow: int = 15
    positioning: int = 15
    risk_reward: int = 10

    @property
    def total(self) -> int:
        return (
            self.htf_trend
            + self.ltf_structure
            + self.resistance_rejection
            + self.momentum
            + self.volume_orderflow
            + self.positioning
            + self.risk_reward
        )


@dataclass(frozen=True)
class ShortScannerConfig:
    quote_asset: str = "USDT"
    interval: str = "15m"
    htf_interval: str = "1h"
    klines_limit: int = 120
    htf_klines_limit: int = 80

    # Liquidity floor: below this 24h quote volume, spreads and thin-book
    # manipulation risk make a futures short unreliable regardless of how
    # good the setup looks.
    min_quote_volume_24h: float = 20_000_000
    # After the liquidity pre-filter (one bulk ticker call), cap how many
    # symbols get the expensive per-symbol kline/funding/OI enrichment --
    # highest 24h quote volume first.
    max_symbols_scanned: int = 40
    scan_time_budget_seconds: float = 45.0

    rsi_period: int = 14
    rsi_overbought: float = 65.0
    ema_fast: int = 20
    ema_slow: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    swing_left: int = 2
    swing_right: int = 2

    # Funding rate (per funding interval, as a fraction -- 0.0003 = 0.03%)
    # above which longs are considered "crowded": paying a real premium to
    # stay long is a contrarian short signal, not just a data point.
    funding_rate_hot: float = 0.0003

    # Scoring bands
    entry_score_min: float = 75
    watch_score_min: float = 60

    weights: ShortScoreWeights = field(default_factory=ShortScoreWeights)


DEFAULT_CONFIG = ShortScannerConfig()
