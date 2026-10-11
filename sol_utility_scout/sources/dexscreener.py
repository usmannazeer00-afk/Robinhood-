"""DexScreener: discovery of fresh Solana tokens plus market data.

Discovery uses the feeds DexScreener publishes for tokens whose teams just
did something on-platform -- a new token profile (team paid to add
description/socials, a decent proxy for "has a team that cares"), a new
boost, or a community takeover. Pure fire-and-forget pump.fun launches
never show up there, which is exactly the filter a utility scout wants.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from ..models import Socials
from .http import get_json, make_session

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.dexscreener.com"
DISCOVERY_FEEDS = ("token-profiles/latest/v1", "token-boosts/latest/v1", "token-boosts/top/v1", "community-takeovers/latest/v1")
BATCH_SIZE = 30  # tokens/v1 accepts up to 30 comma-separated addresses


class DexScreenerClient:
    def __init__(self, api_base: str | None = None, session: requests.Session | None = None, chain_id: str = "solana") -> None:
        self.api_base = (api_base or os.environ.get("DEX_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.session = session or make_session()
        self.chain_id = chain_id

    def discover(self) -> dict[str, dict[str, Any]]:
        """Returns {token_address: profile_entry} across every discovery feed.

        Profile entries carry the description and links; when a token
        appears in several feeds the richest entry (longest description)
        wins and cto/boost flags are merged."""
        found: dict[str, dict[str, Any]] = {}
        for feed in DISCOVERY_FEEDS:
            try:
                items = get_json(self.session, f"{self.api_base}/{feed}")
            except (requests.RequestException, ValueError) as exc:
                logger.warning("dexscreener feed %s failed: %s", feed, exc)
                continue
            for item in items or []:
                if item.get("chainId") != self.chain_id or not item.get("tokenAddress"):
                    continue
                addr = item["tokenAddress"]
                entry = found.setdefault(addr, {"tokenAddress": addr, "description": "", "links": [], "cto": False, "feeds": []})
                entry["feeds"].append(feed.split("/")[0])
                if len(item.get("description") or "") > len(entry["description"]):
                    entry["description"] = item.get("description") or ""
                if item.get("links") and len(item["links"]) > len(entry["links"]):
                    entry["links"] = item["links"]
                if feed.startswith("community-takeovers"):
                    entry["cto"] = True
        return found

    def pairs_for_tokens(self, addresses: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Returns {token_address: [pair, ...]} for every address (base-token side only)."""
        out: dict[str, list[dict[str, Any]]] = {a: [] for a in addresses}
        for i in range(0, len(addresses), BATCH_SIZE):
            batch = addresses[i : i + BATCH_SIZE]
            try:
                pairs = get_json(self.session, f"{self.api_base}/tokens/v1/{self.chain_id}/{','.join(batch)}")
            except (requests.RequestException, ValueError) as exc:
                logger.warning("dexscreener pairs batch failed: %s", exc)
                continue
            for pair in pairs or []:
                base = (pair.get("baseToken") or {}).get("address")
                if base in out:
                    out[base].append(pair)
        return out


def launch_time(pairs: list[dict[str, Any]]) -> datetime | None:
    """The token's launch = its oldest pool's creation time."""
    stamps = [p["pairCreatedAt"] for p in pairs if p.get("pairCreatedAt")]
    if not stamps:
        return None
    return datetime.fromtimestamp(min(stamps) / 1000, tz=timezone.utc)


def main_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The deepest-liquidity pool, which is what price/mcap are read from."""
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


def parse_socials(links: list[dict[str, Any]], pair: dict[str, Any] | None = None) -> Socials:
    """Merges profile links and the pair's info.websites/info.socials into one Socials."""
    socials = Socials()
    entries: list[tuple[str, str, str]] = []  # (type, label, url)
    for link in links or []:
        entries.append(((link.get("type") or "").lower(), (link.get("label") or "").lower(), link.get("url") or ""))
    info = (pair or {}).get("info") or {}
    for site in info.get("websites") or []:
        entries.append(("website", (site.get("label") or "").lower(), site.get("url") or ""))
    for social in info.get("socials") or []:
        entries.append(((social.get("type") or "").lower(), "", social.get("url") or ""))

    for kind, label, url in entries:
        if not url:
            continue
        lowered = url.lower()
        if kind == "twitter" or "x.com/" in lowered or "twitter.com/" in lowered:
            # A link to a single tweet (often someone famous) is not the project's own account.
            if "/status/" in lowered:
                socials.other.append(url)
            else:
                socials.twitter = socials.twitter or url
        elif kind == "telegram" or "t.me/" in lowered:
            socials.telegram = socials.telegram or url
        elif kind == "discord" or "discord." in lowered:
            socials.discord = socials.discord or url
        elif "github.com/" in lowered:
            socials.github = socials.github or url
        elif any(k in label for k in ("doc", "whitepaper", "litepaper", "gitbook")) or "gitbook" in lowered or "/docs" in lowered or lowered.startswith("https://docs."):
            socials.docs = socials.docs or url
        elif kind in ("website", "") or "website" in label:
            socials.website = socials.website or url
        else:
            socials.other.append(url)
    return socials
