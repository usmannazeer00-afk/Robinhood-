"""DexScreener-backed provider.

DexScreener's free public API doesn't expose everything the scoring
rubric wants (no unique-buyer time series, no holder distribution, no
deployer history, no per-token price history array) — those come from a
chain explorer/indexer instead, see ``ExplorerEnrichment`` below. What it
does give us (market cap, liquidity, m5/h1 volume, m5 buy/sell counts) is
enough to populate age, market cap, liquidity, MC/liquidity and an
approximate volume-acceleration signal out of the box.

Once Robinhood Chain has a confirmed DexScreener chainId (or an
equivalent aggregator), set ``DEX_CHAIN_ID`` / ``DEX_API_BASE`` and this
provider works as-is.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

from ..config import SniperConfig
from ..models import DeployerInfo, LiquidityInfo, PricePoint, TokenSnapshot
from .base import PairDataProvider

DEFAULT_API_BASE = "https://api.dexscreener.com"
REQUEST_TIMEOUT_SECONDS = 10
# enrich_by_token runs once per candidate per scan (potentially dozens during
# a launch wave) -- a short timeout bounds the worst case when the API is slow.
ENRICH_TIMEOUT_SECONDS = 5


class ExplorerEnrichment(Protocol):
    """Optional hook to fill in holder/deployer/LP data from a chain explorer."""

    def enrich(self, token: TokenSnapshot) -> TokenSnapshot: ...


class DexScreenerProvider(PairDataProvider):
    def __init__(
        self,
        chain_id: str | None = None,
        search_query: str | None = None,
        api_base: str | None = None,
        enrichment: ExplorerEnrichment | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.chain_id = chain_id or os.environ.get("DEX_CHAIN_ID", "robinhood")
        self.search_query = search_query or os.environ.get("DEX_SEARCH_QUERY", self.chain_id)
        self.api_base = (api_base or os.environ.get("DEX_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.enrichment = enrichment
        self.session = session or requests.Session()

    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        url = f"{self.api_base}/latest/dex/search"
        resp = self.session.get(url, params={"q": self.search_query}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()
        pairs = payload.get("pairs") or []

        snapshots = []
        for raw in pairs:
            if raw.get("chainId") != self.chain_id:
                continue
            snap = self._to_snapshot(raw)
            if snap is None:
                continue
            if snap.age_minutes > config.age_max_minutes:
                continue
            if self.enrichment is not None:
                snap = self.enrichment.enrich(snap)
            snapshots.append(snap)
        return snapshots

    def enrich_by_token(self, snapshot: TokenSnapshot) -> TokenSnapshot:
        """Fills in market cap/liquidity/volume/buys-sells for a snapshot
        discovered elsewhere (e.g. an on-chain PoolCreated watcher) by
        looking the token address up directly, rather than by name search.
        Falls back to the original snapshot if DexScreener hasn't indexed
        the pool yet (common for a pool that's only seconds/minutes old).
        """
        url = f"{self.api_base}/token-pairs/v1/{self.chain_id}/{snapshot.token_address}"
        try:
            resp = self.session.get(url, timeout=ENRICH_TIMEOUT_SECONDS)
            resp.raise_for_status()
            pairs = resp.json() or []
        except (requests.RequestException, ValueError):
            return snapshot
        if not pairs:
            return snapshot

        match = next(
            (p for p in pairs if p.get("pairAddress", "").lower() == snapshot.pair_address.lower()),
            None,
        )
        raw = match or max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        enriched = self._to_snapshot(raw)
        if enriched is None:
            return snapshot

        # On-chain identity/timing is authoritative; DexScreener only fills in market data.
        enriched.pair_address = snapshot.pair_address
        enriched.token_address = snapshot.token_address
        enriched.created_at = snapshot.created_at
        if snapshot.symbol not in ("?", ""):
            enriched.symbol = snapshot.symbol
        if snapshot.name not in ("?", ""):
            enriched.name = snapshot.name
        return enriched

    @staticmethod
    def _to_snapshot(raw: dict[str, Any]) -> TokenSnapshot | None:
        base = raw.get("baseToken") or {}
        created_ms = raw.get("pairCreatedAt")
        if not created_ms or not base.get("address"):
            return None

        liquidity = raw.get("liquidity") or {}
        volume = raw.get("volume") or {}
        txns_m5 = ((raw.get("txns") or {}).get("m5")) or {}

        vol_m5 = float(volume.get("m5") or 0.0)
        vol_h1 = float(volume.get("h1") or 0.0)
        # DexScreener doesn't break out 1m/15m directly; approximate a
        # per-minute rate from the 5m bucket and a 15m rate by blending
        # the 5m and 1h buckets. This is a coarse proxy, not a precise read.
        approx_vol_1m = vol_m5 / 5 if vol_m5 else 0.0
        approx_vol_15m = (vol_m5 + vol_h1 / 4) / 2 if (vol_m5 or vol_h1) else 0.0

        price_usd = float(raw.get("priceUsd") or 0.0)

        return TokenSnapshot(
            chain=raw.get("chainId", ""),
            pair_address=raw.get("pairAddress", ""),
            token_address=base.get("address", ""),
            symbol=base.get("symbol", "?"),
            name=base.get("name", "?"),
            created_at=datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc),
            market_cap_usd=float(raw.get("marketCap") or raw.get("fdv") or 0.0),
            liquidity=LiquidityInfo(usd=float(liquidity.get("usd") or 0.0)),
            volume_1m=approx_vol_1m,
            volume_5m=vol_m5,
            volume_15m=approx_vol_15m,
            volume_5m_buckets=[vol_m5] if vol_m5 else [],
            buys=int(txns_m5.get("buys") or 0),
            sells=int(txns_m5.get("sells") or 0),
            unique_buyers_series=[],
            price_history=[PricePoint(datetime.now(timezone.utc), price_usd)] if price_usd else [],
            deployer=DeployerInfo(),
        )
