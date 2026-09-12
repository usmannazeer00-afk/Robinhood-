"""Synthetic provider for demos and tests.

Generates a handful of candidate tokens spanning the full quality range
(a clean accelerating setup, a wash-trading look-alike, a rug-flagged
deployer, etc.) so /newpair has something to show before a real
Robinhood Chain data source is wired up.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from ..config import SniperConfig
from ..models import DeployerInfo, LiquidityInfo, PricePoint, TokenSnapshot
from .base import PairDataProvider


def _price_path(launch: float, peak_mult: float, retrace_pct: float, points: int = 6) -> list[PricePoint]:
    now = datetime.now(timezone.utc)
    peak = launch * peak_mult
    trough = peak * (1 - retrace_pct / 100)
    higher_low = max(trough, launch * 1.05)
    path_prices = [launch, peak, higher_low, higher_low * 1.08, higher_low * 1.15]
    step = timedelta(minutes=20 / max(points, 1))
    start = now - step * (len(path_prices) - 1)
    return [PricePoint(start + step * i, p) for i, p in enumerate(path_prices)]


class MockProvider(PairDataProvider):
    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        now = datetime.now(timezone.utc)
        candidates = [
            TokenSnapshot(
                chain=config.chain,
                pair_address="0xPAIR_STRONG",
                token_address="0xTOKEN_STRONG",
                symbol="SNIPE",
                name="Sniper Prime",
                created_at=now - timedelta(minutes=18),
                market_cap_usd=190_000,
                liquidity=LiquidityInfo(usd=62_000, locked_or_burned=True),
                volume_1m=1_400,
                volume_5m=6_500,
                volume_15m=15_000,
                volume_5m_buckets=[8_000, 18_000, 35_000],
                buys=310,
                sells=150,
                unique_buyers_series=[30, 55, 90, 140],
                price_history=_price_path(launch=0.00010, peak_mult=2.2, retrace_pct=32),
                top10_holder_pct=27.0,
                top20_holder_pct=41.0,
                deployer=DeployerInfo(address="0xDEPLOYER_CLEAN"),
            ),
            TokenSnapshot(
                chain=config.chain,
                pair_address="0xPAIR_WASH",
                token_address="0xTOKEN_WASH",
                symbol="FAKEVOL",
                name="Suspicious Volume Inc",
                created_at=now - timedelta(minutes=25),
                market_cap_usd=220_000,
                liquidity=LiquidityInfo(usd=45_000, locked_or_burned=False, freely_removable_pct=80),
                volume_1m=2_000,
                volume_5m=9_000,
                volume_15m=27_000,
                volume_5m_buckets=[9_000, 9_400, 9_100],
                buys=520,
                sells=500,
                unique_buyers_series=[300, 310, 315],
                price_history=_price_path(launch=0.00020, peak_mult=4.0, retrace_pct=10),
                top10_holder_pct=38.0,
                top20_holder_pct=55.0,
                deployer=DeployerInfo(address="0xDEPLOYER_WASH"),
            ),
            TokenSnapshot(
                chain=config.chain,
                pair_address="0xPAIR_RUG",
                token_address="0xTOKEN_RUG",
                symbol="RUGWARN",
                name="Definitely Fine Token",
                created_at=now - timedelta(minutes=9),
                market_cap_usd=130_000,
                liquidity=LiquidityInfo(usd=20_000, locked_or_burned=False, freely_removable_pct=95),
                volume_1m=900,
                volume_5m=4_000,
                volume_15m=9_000,
                volume_5m_buckets=[3_000, 4_000, 5_000],
                buys=80,
                sells=60,
                unique_buyers_series=[20, 30, 45],
                price_history=_price_path(launch=0.00005, peak_mult=1.8, retrace_pct=28),
                top10_holder_pct=52.0,
                top20_holder_pct=68.0,
                deployer=DeployerInfo(address="0xDEPLOYER_RUG", owns_large_pct=True, has_sold=True),
            ),
            TokenSnapshot(
                chain=config.chain,
                pair_address="0xPAIR_TOOSOON",
                token_address="0xTOKEN_TOOSOON",
                symbol="NEWBORN",
                name="Just Launched",
                created_at=now - timedelta(minutes=2),
                market_cap_usd=95_000,
                liquidity=LiquidityInfo(usd=50_000, locked_or_burned=True),
                volume_1m=3_000,
                volume_5m=8_000,
                volume_15m=8_500,
                volume_5m_buckets=[8_000],
                buys=60,
                sells=10,
                unique_buyers_series=[15, 40],
                price_history=_price_path(launch=0.00003, peak_mult=1.3, retrace_pct=15),
                top10_holder_pct=22.0,
                top20_holder_pct=33.0,
                deployer=DeployerInfo(address="0xDEPLOYER_NEW"),
            ),
        ]
        return candidates
