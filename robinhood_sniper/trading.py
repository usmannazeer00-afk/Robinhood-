"""Buying FOMO picks.

`PaperTrader` is the only executor implemented: it records the buy (entry
price, size, time) to a JSON positions file instead of sending a
transaction, so you can check how the BUY signals actually perform
before risking money. A live executor plugs in behind the same
`BuyExecutor` interface.

Guard rails apply to any executor: a fixed USD size per buy, a cap on
open positions, and never buying the same token twice.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import DEFAULT_CONFIG, SniperConfig
from .fomo import DEFAULT_FOMO_CONFIG, FomoAction, FomoConfig, FomoResult, evaluate, rank
from .models import TokenSnapshot
from .providers.base import PairDataProvider
from .providers.dexscreener import DexScreenerProvider
from .scanner import scan
from .smart_wallets import SmartWalletBook

DEFAULT_POSITIONS_PATH = os.environ.get("SNIPER_POSITIONS_PATH", ".robinhood_sniper_positions.json")


@dataclass
class Position:
    token_address: str
    pair_address: str
    symbol: str
    entry_price_usd: float
    size_usd: float
    opened_at: str
    mode: str  # "paper" or "live"
    fomo_score: float
    sniper_score: float


class BuyExecutor(Protocol):
    def holds(self, token_address: str) -> bool: ...

    def open_positions(self) -> list[Position]: ...

    def buy(self, token: TokenSnapshot, size_usd: float, fomo_score: float, sniper_score: float) -> Position: ...


class PaperTrader:
    mode = "paper"

    def __init__(self, path: str | None = None, persist: bool = True) -> None:
        self.path = Path(path or DEFAULT_POSITIONS_PATH)
        self.persist = persist
        self._positions: list[Position] = self._load() if persist else []

    def _load(self) -> list[Position]:
        if not self.path.exists():
            return []
        try:
            return [Position(**row) for row in json.loads(self.path.read_text())]
        except (json.JSONDecodeError, OSError, TypeError):
            return []

    def _save(self) -> None:
        if self.persist:
            self.path.write_text(json.dumps([asdict(p) for p in self._positions], indent=1))

    def holds(self, token_address: str) -> bool:
        return any(p.token_address.lower() == token_address.lower() for p in self._positions)

    def open_positions(self) -> list[Position]:
        return list(self._positions)

    def buy(self, token: TokenSnapshot, size_usd: float, fomo_score: float, sniper_score: float) -> Position:
        price = token.price_history[-1].price_usd if token.price_history else 0.0
        position = Position(
            token_address=token.token_address,
            pair_address=token.pair_address,
            symbol=token.symbol,
            entry_price_usd=price,
            size_usd=size_usd,
            opened_at=datetime.now(timezone.utc).isoformat(),
            mode=self.mode,
            fomo_score=fomo_score,
            sniper_score=sniper_score,
        )
        self._positions.append(position)
        self._save()
        return position


@dataclass(frozen=True)
class BuyPolicy:
    size_usd: float = 25.0
    max_open_positions: int = 5


@dataclass
class FomoRun:
    results: list[FomoResult]
    bought: list[Position]
    skipped_buys: list[tuple[FomoResult, str]]  # BUY signals not executed, and why
    newly_smart: list[str]


def run_fomo(
    provider: PairDataProvider,
    book: SmartWalletBook,
    executor: BuyExecutor | None = None,
    policy: BuyPolicy = BuyPolicy(),
    sniper_cfg: SniperConfig = DEFAULT_CONFIG,
    fomo_cfg: FomoConfig = DEFAULT_FOMO_CONFIG,
) -> FomoRun:
    """Scan, learn smart wallets from history, score FOMO, and buy BUY
    signals through `executor` (pass None to only report signals)."""
    scanned = scan(provider, sniper_cfg)

    # Descriptions (the token's "story") only come from DexScreener's
    # profiles feed, not the pair data the scan already pulled.
    dex = provider if isinstance(provider, DexScreenerProvider) else getattr(provider, "dexscreener", None)
    if dex is not None and scanned:
        profiles = dex.fetch_latest_profiles()
        for s in scanned:
            row = profiles.get(s.token.token_address.lower())
            if row:
                DexScreenerProvider.apply_profile_row(s.token, row)

    newly_smart: list[str] = []
    history = getattr(provider, "history", None)
    if history is not None:
        newly_smart = book.learn_from_history(history)
        book.save()

    results = rank([evaluate(s, book, fomo_cfg, sniper_cfg) for s in scanned])

    bought: list[Position] = []
    skipped: list[tuple[FomoResult, str]] = []
    if executor is not None:
        for r in results:
            if r.action is not FomoAction.BUY:
                continue
            if executor.holds(r.token.token_address):
                skipped.append((r, "already holding"))
                continue
            if len(executor.open_positions()) >= policy.max_open_positions:
                skipped.append((r, f"max {policy.max_open_positions} open positions"))
                continue
            bought.append(executor.buy(r.token, policy.size_usd, r.score, r.scanned.result.total))
    return FomoRun(results, bought, skipped, newly_smart)
