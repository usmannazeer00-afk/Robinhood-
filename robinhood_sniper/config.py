"""Tunable parameters for the Robinhood Chain sniper filter.

Defaults mirror the strategy spec: age/mcap/liquidity ranges, the volume
and buyer acceleration checks, holder/deployer hard filters, and the
100-point scoring rubric with its 🟢/🟡/🔴 bands.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoreWeights:
    age: int = 10
    market_cap: int = 10
    liquidity: int = 15
    mcap_to_liquidity: int = 5
    volume_acceleration: int = 20
    buy_sell_ratio: int = 10
    buyer_acceleration: int = 10
    price_structure: int = 10
    holder_distribution: int = 5
    deployer_lp: int = 5

    @property
    def total(self) -> int:
        return (
            self.age
            + self.market_cap
            + self.liquidity
            + self.mcap_to_liquidity
            + self.volume_acceleration
            + self.buy_sell_ratio
            + self.buyer_acceleration
            + self.price_structure
            + self.holder_distribution
            + self.deployer_lp
        )


@dataclass(frozen=True)
class SniperConfig:
    chain: str = "robinhood"

    # 1. Token age (minutes)
    age_min_minutes: float = 5
    age_max_minutes: float = 45
    age_ideal_min: float = 10
    age_ideal_max: float = 30

    # 2. Market cap (USD)
    mcap_min: float = 75_000
    mcap_max: float = 400_000
    mcap_sweet_min: float = 100_000
    mcap_sweet_max: float = 300_000

    # 3. Liquidity (USD)
    liquidity_min: float = 40_000
    liquidity_preferred_min: float = 40_000
    liquidity_preferred_max: float = 100_000
    mcap_to_liquidity_max: float = 8.0

    # 5. Buy/sell ratio
    buy_sell_ratio_min: float = 1.5
    buy_sell_ratio_preferred_min: float = 1.7
    buy_sell_ratio_preferred_max: float = 2.5

    # 8. Holder distribution (excluding LP / burn / known CEX-router contracts)
    top10_holder_pct_max: float = 35.0
    top10_holder_pct_good: float = 30.0
    top20_holder_pct_max: float = 50.0
    top20_holder_pct_good: float = 45.0

    # Price structure: pump -> retracement -> higher low
    retracement_min_pct: float = 20.0
    retracement_max_pct: float = 40.0

    # Scoring bands
    entry_score_min: int = 80
    watch_score_min: int = 65

    weights: ScoreWeights = field(default_factory=ScoreWeights)


DEFAULT_CONFIG = SniperConfig()
