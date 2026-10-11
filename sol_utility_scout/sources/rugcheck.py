"""RugCheck.xyz full token report -> SafetyReport.

The free public report gives mint/freeze authority, LP lock percentage,
top holders (flagged insider or not), total holder count and RugCheck's
own risk list. Top-holder concentration here excludes the AMM pool and
locker accounts RugCheck itself labels -- otherwise the pool's own
reserve (often 50-90% of supply) makes every token look concentrated.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from ..models import SafetyReport
from .http import get_json, make_session

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.rugcheck.xyz/v1"
EXCLUDED_ACCOUNT_TYPES = {"AMM", "LOCKER", "BURN"}


class RugCheckClient:
    def __init__(self, api_base: str | None = None, session: requests.Session | None = None) -> None:
        self.api_base = (api_base or os.environ.get("RUGCHECK_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.session = session or make_session()

    def report(self, mint: str) -> SafetyReport:
        try:
            data = get_json(self.session, f"{self.api_base}/tokens/{mint}/report", timeout=15)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("rugcheck report for %s failed: %s", mint, exc)
            return SafetyReport(available=False)
        return parse_report(data or {})


def parse_report(data: dict[str, Any]) -> SafetyReport:
    if not data:
        return SafetyReport(available=False)

    known = data.get("knownAccounts") or {}
    excluded = {addr for addr, meta in known.items() if (meta or {}).get("type") in EXCLUDED_ACCOUNT_TYPES}
    for market in data.get("markets") or []:
        for key in ("pubkey", "liquidityA", "liquidityB"):
            if market.get(key):
                excluded.add(market[key])

    holders = [
        h for h in data.get("topHolders") or []
        if h.get("owner") not in excluded and h.get("address") not in excluded
    ]
    top10 = sum(float(h.get("pct") or 0) for h in holders[:10]) if data.get("topHolders") is not None else None

    lp_locked = None
    markets = data.get("markets") or []
    if markets:
        main = max(markets, key=lambda m: ((m.get("lp") or {}).get("quoteUSD") or 0))
        lp_locked = (main.get("lp") or {}).get("lpLockedPct")

    risks = data.get("risks") or []
    launchpad = data.get("launchpad")
    return SafetyReport(
        available=True,
        mint_revoked=data.get("mintAuthority") is None,
        freeze_revoked=data.get("freezeAuthority") is None,
        lp_locked_pct=float(lp_locked) if lp_locked is not None else None,
        top10_pct=top10,
        holders=data.get("totalHolders"),
        rugged=bool(data.get("rugged")),
        insiders_detected=int(data.get("graphInsidersDetected") or 0),
        danger_risks=[r.get("name", "") for r in risks if r.get("level") == "danger"],
        warn_risks=[r.get("name", "") for r in risks if r.get("level") == "warn"],
        creator=data.get("creator"),
        launchpad=(launchpad or {}).get("name") if isinstance(launchpad, dict) else launchpad,
    )
