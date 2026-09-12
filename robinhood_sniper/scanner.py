"""Ties a data provider to the scoring engine and ranks the results."""

from __future__ import annotations

from dataclasses import dataclass

from .config import SniperConfig, DEFAULT_CONFIG
from .models import TokenSnapshot
from .providers.base import PairDataProvider
from .scoring import ScoreResult, score_token


@dataclass
class ScannedToken:
    token: TokenSnapshot
    result: ScoreResult


def scan(
    provider: PairDataProvider,
    config: SniperConfig = DEFAULT_CONFIG,
    min_score: float = 0,
) -> list[ScannedToken]:
    tokens = provider.fetch_new_pairs(config)
    scanned = [ScannedToken(t, score_token(t, config)) for t in tokens]
    scanned = [s for s in scanned if s.result.total >= min_score]
    scanned.sort(key=lambda s: s.result.total, reverse=True)
    return scanned
