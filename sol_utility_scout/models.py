"""Data shapes shared between sources, rating, conviction and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class Socials:
    website: str | None = None
    twitter: str | None = None
    telegram: str | None = None
    discord: str | None = None
    github: str | None = None
    docs: str | None = None
    other: list[str] = field(default_factory=list)

    def present(self) -> list[str]:
        return [name for name in ("website", "twitter", "telegram", "discord", "github", "docs") if getattr(self, name)]


@dataclass
class WebsiteProbe:
    reachable: bool = False
    status: int | None = None
    title: str = ""
    text_length: int = 0
    utility_hits: list[str] = field(default_factory=list)
    has_docs_link: bool = False
    has_github_link: bool = False
    has_app_link: bool = False
    has_api_link: bool = False  # a public API: api.* subdomain or /api path
    has_bot_link: bool = False  # a Telegram bot (t.me/<name>bot) -- a shipped product
    error: str | None = None


@dataclass
class SafetyReport:
    available: bool = False
    mint_revoked: bool | None = None
    freeze_revoked: bool | None = None
    lp_locked_pct: float | None = None
    top10_pct: float | None = None  # excludes AMM pool / locker accounts
    holders: int | None = None
    rugged: bool = False
    insiders_detected: int = 0
    danger_risks: list[str] = field(default_factory=list)
    warn_risks: list[str] = field(default_factory=list)
    creator: str | None = None
    launchpad: str | None = None


@dataclass
class Snapshot:
    """One observation of a token at one point in time."""

    token_address: str
    observed_at: str  # ISO-8601 UTC
    name: str = ""
    symbol: str = ""
    description: str = ""
    pair_address: str | None = None
    dex_id: str | None = None
    launched_at: str | None = None  # ISO-8601 UTC of the oldest pool
    price_usd: float = 0.0
    market_cap: float = 0.0
    liquidity_usd: float = 0.0
    volume_h24: float = 0.0
    volume_h1: float = 0.0
    buys_h24: int = 0
    sells_h24: int = 0
    price_change_h24: float = 0.0
    boosts_active: int = 0
    cto: bool = False
    socials: Socials = field(default_factory=Socials)
    website: WebsiteProbe | None = None
    telegram_members: int | None = None
    safety: SafetyReport = field(default_factory=SafetyReport)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["socials"] = Socials(**_known(Socials, data.get("socials") or {}))
        kwargs["safety"] = SafetyReport(**_known(SafetyReport, data.get("safety") or {}))
        if data.get("website") is not None:
            kwargs["website"] = WebsiteProbe(**_known(WebsiteProbe, data["website"]))
        return cls(**kwargs)


def _known(cls, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class Rating:
    total: float
    utility: float
    socials: float
    safety: float
    market: float
    traction: float
    category: str  # e.g. "AI", "DeFi", "meme"
    rejected: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tech_proof: list[str] = field(default_factory=list)  # github / docs / app
