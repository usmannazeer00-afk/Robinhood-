"""Provider interface: anything that can list new pairs on a chain.

Swap in a real implementation once you have a concrete data source for
Robinhood Chain (a DexScreener-style aggregator, a chain-native indexer,
or a subgraph). The scanner only depends on this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import SniperConfig
from ..models import TokenSnapshot


class PairDataProvider(ABC):
    @abstractmethod
    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        """Return candidate pairs younger than config.age_max_minutes."""
        raise NotImplementedError
