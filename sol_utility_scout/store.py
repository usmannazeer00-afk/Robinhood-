"""JSON-on-disk watchlist so each cron/bot cycle shares state.

Layout:
    {
      "tokens":   {address: record},   # watched, completed and dropped tokens
      "rejected": {address: {"at": iso, "reasons": [...]}},  # recheck cache
    }

A record keeps the latest full Snapshot, a compact time series for
charts/structure, the conviction history and the event log.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_STORE_PATH

MAX_SERIES = 2_000  # 7 days at a 5-minute cadence fits comfortably

WATCHING, COMPLETED, DROPPED = "watching", "completed", "dropped"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or DEFAULT_STORE_PATH)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        data.setdefault("tokens", {})
        data.setdefault("rejected", {})
        return data

    def save(self) -> None:
        # Write-then-rename so a crash mid-write never corrupts the watchlist.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".scout-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh)
        os.replace(tmp, self.path)

    # --- tokens
    @property
    def tokens(self) -> dict[str, dict[str, Any]]:
        return self.data["tokens"]

    def get(self, address: str) -> dict[str, Any] | None:
        return self.tokens.get(address)

    def by_status(self, status: str) -> list[dict[str, Any]]:
        return [r for r in self.tokens.values() if r["status"] == status]

    def find(self, query: str) -> dict[str, Any] | None:
        """Lookup by exact address, or case-insensitive symbol (most recent first)."""
        if query in self.tokens:
            return self.tokens[query]
        q = query.lstrip("$").lower()
        matches = [r for r in self.tokens.values() if (r.get("symbol") or "").lower() == q]
        return max(matches, key=lambda r: r["first_seen"]) if matches else None

    def append_series(self, record: dict[str, Any], point: list[Any]) -> None:
        record["series"].append(point)
        if len(record["series"]) > MAX_SERIES:
            # keep the first point (launch reference) and thin the oldest half
            head, rest = record["series"][:1], record["series"][1:]
            half = len(rest) // 2
            record["series"] = head + rest[:half:2] + rest[half:]

    # --- rejected cache
    def recently_rejected(self, address: str, hours: float) -> bool:
        entry = self.data["rejected"].get(address)
        if not entry:
            return False
        return datetime.fromisoformat(entry["at"]) > datetime.now(timezone.utc) - timedelta(hours=hours)

    def mark_rejected(self, address: str, reasons: list[str]) -> None:
        self.data["rejected"][address] = {"at": now_iso(), "reasons": reasons}

    def prune_rejected(self, older_than_hours: float) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        self.data["rejected"] = {
            a: e for a, e in self.data["rejected"].items() if datetime.fromisoformat(e["at"]) > cutoff
        }
