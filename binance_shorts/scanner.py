"""Ties a data provider to the scoring engine and ranks the results."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_CONFIG, ShortScannerConfig
from .models import FuturesSnapshot
from .providers.base import FuturesDataProvider
from .scoring import ShortScoreResult, score_short_setup


@dataclass
class ScannedSymbol:
    snapshot: FuturesSnapshot
    result: ShortScoreResult


def scan(
    provider: FuturesDataProvider,
    config: ShortScannerConfig = DEFAULT_CONFIG,
    min_score: float = 0,
) -> list[ScannedSymbol]:
    snapshots = provider.fetch_candidates(config)
    scanned = [ScannedSymbol(s, score_short_setup(s, config)) for s in snapshots]
    scanned = [s for s in scanned if s.result.total >= min_score]
    scanned.sort(key=lambda s: s.result.total, reverse=True)
    return scanned


def best_short(provider: FuturesDataProvider, config: ShortScannerConfig = DEFAULT_CONFIG) -> ScannedSymbol | None:
    """The single highest-scoring candidate from a fresh scan, or None if
    nothing was returned at all (not just nothing that scored well --
    every fetched symbol comes back, min_score defaults to 0)."""
    results = scan(provider, config)
    return results[0] if results else None
