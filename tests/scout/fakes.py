"""Offline stand-ins for DexScreener / RugCheck / web so the tracker can be
driven through a full 7-day lifecycle in tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sol_utility_scout.models import SafetyReport, WebsiteProbe

UTILITY_DESC = (
    "Ledgerly is an AI agent trading terminal and analytics dashboard for Solana. "
    "Read the docs and whitepaper, roadmap and tokenomics live; audit by OtterSec. Launch app in beta."
)


def make_pair(addr: str, *, hours_old: float = 3, price: float = 0.001, mcap: float = 300_000, liq: float = 60_000,
              holders_hint: int = 0, socials: bool = True, boosts: int = 0) -> dict:
    created = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    info = {}
    if socials:
        info = {
            "websites": [{"label": "Website", "url": "https://ledgerly.example"}],
            "socials": [{"type": "twitter", "url": "https://x.com/ledgerly"}, {"type": "telegram", "url": "https://t.me/ledgerly"}],
        }
    return {
        "chainId": "solana", "dexId": "raydium", "pairAddress": f"pool-{addr}",
        "baseToken": {"address": addr, "name": "Ledgerly", "symbol": "LDGR"},
        "priceUsd": str(price), "marketCap": mcap, "liquidity": {"usd": liq},
        "volume": {"h24": 400_000, "h1": 20_000}, "txns": {"h24": {"buys": 900, "sells": 600}},
        "priceChange": {"h24": 12}, "pairCreatedAt": int(created.timestamp() * 1000),
        "info": info, "boosts": {"active": boosts},
    }


class FakeDex:
    def __init__(self) -> None:
        self.profiles: dict[str, dict] = {}
        self.pairs: dict[str, list[dict]] = {}

    def discover(self):
        return {a: dict(p) for a, p in self.profiles.items()}

    def pairs_for_tokens(self, addresses):
        return {a: list(self.pairs.get(a, [])) for a in addresses}


class FakeRugCheck:
    def __init__(self) -> None:
        self.reports: dict[str, SafetyReport] = {}

    def report(self, mint):
        return self.reports.get(mint, SafetyReport(available=False))


def good_safety(holders: int = 600) -> SafetyReport:
    return SafetyReport(available=True, mint_revoked=True, freeze_revoked=True, lp_locked_pct=100, top10_pct=18, holders=holders)


class FakeWeb:
    def __init__(self) -> None:
        self.up = True
        self.members = 400

    def website(self, url):
        if not self.up:
            return WebsiteProbe(reachable=False, error="ConnectionError"), ""
        text = "Ledgerly terminal docs whitepaper roadmap github launch app " * 20
        return WebsiteProbe(reachable=True, status=200, title="Ledgerly", text_length=len(text),
                            utility_hits=["docs", "whitepaper"], has_docs_link=True, has_github_link=True, has_app_link=True), text

    def telegram_members(self, url):
        return self.members
