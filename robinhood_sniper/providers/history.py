"""Persistent per-pool history across scans.

Each CLI/cron run is a fresh process, so without this, unique-buyer
acceleration and price-structure scoring can never have more than one
data point — they're permanently stuck at 0/10 and 0/10 for every real
token. This stores two real, on-chain-derived series to disk, keyed by
pool address, so repeated scans build up an actual history:

  * price points — sampled from DexScreener's live price at each scan
  * unique buyer wallets — derived from real Swap events on the pool,
    using each swap's transaction sender (the actual trading wallet),
    not the Swap event's own `sender` field (which is usually just the
    router contract and would undercount distinct traders)

JSON on disk so a recurring cron job (a new process each fire) shares
state between iterations.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_PATH = os.environ.get("SNIPER_HISTORY_PATH", ".robinhood_sniper_history.json")


def _empty_entry() -> dict[str, Any]:
    return {
        "pool_created_block": None,
        "last_scanned_block": None,
        "price_points": [],  # [[iso_timestamp, price_usd], ...]
        "buyer_wallets": [],  # cumulative distinct wallet addresses (lowercase)
        "buyer_series": [],  # [[iso_timestamp, cumulative_unique_count], ...]
    }


class TokenHistoryStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or DEFAULT_HISTORY_PATH)
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._data))
        except OSError:
            pass  # best-effort; a lost write just means one scan's history doesn't persist

    def get(self, pool_address: str) -> dict[str, Any]:
        return self._data.setdefault(pool_address.lower(), _empty_entry())

    def record_price(self, pool_address: str, price_usd: float) -> None:
        if price_usd <= 0:
            return
        entry = self.get(pool_address)
        entry["price_points"].append([datetime.now(timezone.utc).isoformat(), price_usd])

    def record_buyers(self, pool_address: str, new_wallets: set[str], last_block: int) -> None:
        entry = self.get(pool_address)
        known = set(entry["buyer_wallets"]) | {w.lower() for w in new_wallets}
        entry["buyer_wallets"] = sorted(known)
        entry["buyer_series"].append([datetime.now(timezone.utc).isoformat(), len(known)])
        entry["last_scanned_block"] = last_block

    def price_series(self, pool_address: str) -> list[tuple[datetime, float]]:
        entry = self.get(pool_address)
        return [(datetime.fromisoformat(ts), price) for ts, price in entry["price_points"]]

    def buyer_series(self, pool_address: str) -> list[int]:
        entry = self.get(pool_address)
        return [count for _, count in entry["buyer_series"]]

    def prune(self, max_age_minutes: float) -> None:
        """Drops entries whose last price sample is well past the sniping window."""
        cutoff = time.time() - max_age_minutes * 60 * 3
        for key in list(self._data.keys()):
            points = self._data[key].get("price_points", [])
            if not points:
                continue
            last_ts = datetime.fromisoformat(points[-1][0]).timestamp()
            if last_ts < cutoff:
                del self._data[key]
