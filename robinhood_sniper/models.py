"""Data model for a candidate token/pair pulled from a chain data provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class DeployerInfo:
    address: str | None = None
    owns_large_pct: bool = False
    has_sold: bool = False
    created_multiple_failed_tokens: bool = False
    linked_to_launch_bundles: bool = False
    sends_to_fresh_wallets_before_selling: bool = False

    @property
    def is_clean(self) -> bool:
        return not (
            self.owns_large_pct
            or self.has_sold
            or self.created_multiple_failed_tokens
            or self.linked_to_launch_bundles
            or self.sends_to_fresh_wallets_before_selling
        )


@dataclass
class LiquidityInfo:
    usd: float = 0.0
    locked_or_burned: bool = False
    freely_removable_pct: float = 0.0  # % of LP the deployer/owner can pull instantly

    @property
    def is_healthy(self) -> bool:
        return self.locked_or_burned or self.freely_removable_pct <= 10.0


@dataclass
class PricePoint:
    timestamp: datetime
    price_usd: float


@dataclass
class TokenSnapshot:
    """A point-in-time read of a token/pair, as returned by a data provider."""

    chain: str
    pair_address: str
    token_address: str
    symbol: str
    name: str
    created_at: datetime

    market_cap_usd: float
    liquidity: LiquidityInfo

    # Volume in USD over the trailing window ending "now".
    volume_1m: float = 0.0
    volume_5m: float = 0.0
    volume_15m: float = 0.0
    # Consecutive, non-overlapping 5-minute volume buckets, oldest first,
    # e.g. [prev_5m, current_5m] or [prev_5m, current_5m, next_5m].
    volume_5m_buckets: list[float] = field(default_factory=list)

    buys: int = 0
    sells: int = 0

    # Unique buyer counts sampled over consecutive windows, oldest first,
    # e.g. [30, 55, 90, 140].
    unique_buyers_series: list[int] = field(default_factory=list)

    # Price history since launch, oldest first.
    price_history: list[PricePoint] = field(default_factory=list)

    top10_holder_pct: float | None = None  # excludes LP / burn / known routers
    top20_holder_pct: float | None = None

    deployer: DeployerInfo = field(default_factory=DeployerInfo)

    # Earliest known pool-creation time across ALL of this token's pools, not
    # just this one. A token can spin up new pools long after its real launch
    # (e.g. an established token re-pooled a month later) -- that new pool's
    # own creation time looks like a fresh listing unless this catches it.
    # None means unknown/not checked (e.g. providers other than onchain).
    token_first_pool_created_at: datetime | None = None

    @property
    def age_minutes(self) -> float:
        now = datetime.now(timezone.utc)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (now - created).total_seconds() / 60.0)

    @property
    def mcap_to_liquidity(self) -> float | None:
        if not self.liquidity.usd:
            return None
        return self.market_cap_usd / self.liquidity.usd

    @property
    def buy_sell_ratio(self) -> float | None:
        if not self.sells:
            return None
        return self.buys / self.sells

    @property
    def dexscreener_url(self) -> str:
        return f"https://dexscreener.com/{self.chain}/{self.pair_address}"
