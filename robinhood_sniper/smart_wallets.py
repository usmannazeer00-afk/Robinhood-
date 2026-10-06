"""Smart wallets: addresses with a track record of buying winners early.

Two ways a wallet gets in the book:

  * manual -- you add it (`wallets add 0x... --label "known trader"`).
    Manual wallets always count as smart.
  * learned -- the bot finds it on its own from the scan history. Every
    pool the on-chain provider tracks keeps its first buyers in order and
    a price series. When a pool runs (its tracked price reaches
    `min_multiple` x its first sample), each wallet among its first
    `early_n` buyers is credited with a win for that pool. A learned
    wallet counts as smart once it has `min_wins` wins on different
    pools -- one lucky entry isn't a track record.

Pools flagged as launch bundles are skipped when learning: their earliest
"buyers" are the deployer's own wallets, and crediting them would teach
the bot to follow insiders into the next rug.

Stored as JSON on disk (default `.robinhood_sniper_smart_wallets.json`,
override with SNIPER_SMART_WALLETS_PATH) so it keeps improving across
cron-driven scans.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .providers.history import TokenHistoryStore

DEFAULT_SMART_WALLETS_PATH = os.environ.get(
    "SNIPER_SMART_WALLETS_PATH", ".robinhood_sniper_smart_wallets.json"
)


class SmartWalletBook:
    def __init__(self, path: str | None = None, min_wins: int = 2, persist: bool = True) -> None:
        self.path = Path(path or DEFAULT_SMART_WALLETS_PATH)
        self.min_wins = min_wins
        self.persist = persist
        self._wallets: dict[str, dict[str, Any]] = self._load() if persist else {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self) -> None:
        if not self.persist:
            return
        try:
            self.path.write_text(json.dumps(self._wallets, indent=1))
        except OSError:
            pass

    def _entry(self, address: str) -> dict[str, Any]:
        return self._wallets.setdefault(
            address.lower(),
            {"label": None, "manual": False, "win_pools": [], "best_multiple": 0.0},
        )

    def add(self, address: str, label: str | None = None) -> None:
        entry = self._entry(address)
        entry["manual"] = True
        if label:
            entry["label"] = label

    def remove(self, address: str) -> bool:
        return self._wallets.pop(address.lower(), None) is not None

    def is_smart(self, address: str) -> bool:
        entry = self._wallets.get(address.lower())
        if entry is None:
            return False
        return entry["manual"] or len(entry["win_pools"]) >= self.min_wins

    def describe(self, address: str) -> str:
        entry = self._wallets.get(address.lower(), {})
        short = f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address
        if entry.get("label"):
            return f"{entry['label']} ({short})"
        wins = len(entry.get("win_pools", []))
        if wins:
            return f"{short} [{wins} early wins, best {entry.get('best_multiple', 0):.1f}x]"
        return short

    def smart_wallets(self) -> list[str]:
        return [addr for addr in self._wallets if self.is_smart(addr)]

    def learn_from_history(
        self, history: TokenHistoryStore, min_multiple: float = 3.0, early_n: int = 15
    ) -> list[str]:
        """Credits early buyers of pools that ran. Returns wallets that became
        smart as a result of this call. Idempotent per (wallet, pool)."""
        before = set(self.smart_wallets())
        for pool_key, entry in history.entries().items():
            if entry.get("bundle_detected"):
                continue
            prices = [price for _, price in entry.get("price_points", []) if price > 0]
            if len(prices) < 2:
                continue
            multiple = max(prices) / prices[0]
            if multiple < min_multiple:
                continue
            for wallet in entry.get("early_buyers", [])[:early_n]:
                w = self._entry(wallet)
                if pool_key not in w["win_pools"]:
                    w["win_pools"].append(pool_key)
                w["best_multiple"] = max(w["best_multiple"], multiple)
        return sorted(set(self.smart_wallets()) - before)
