"""Provider interface: anything that can supply candidates for the 15m
Binance USDT-M futures short scanner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import ShortScannerConfig
from ..models import FuturesSnapshot


class FuturesDataProvider(ABC):
    @abstractmethod
    def fetch_candidates(self, config: ShortScannerConfig) -> list[FuturesSnapshot]:
        """Return snapshots for symbols worth scoring (already liquidity-filtered)."""
        raise NotImplementedError
