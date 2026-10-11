"""Post-launch event detection: what changed between two snapshots?

Every tracked token is re-observed each cycle; anything notable that
happened since the last look becomes an Event in the token's log (and a
Telegram alert, if the bot is running). Each event carries a conviction
weight -- positive things the team did or the market showed push
conviction up, red flags push it down.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Snapshot

HOLDER_MILESTONES = (250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000)
MCAP_MULTIPLES = (2, 3, 5, 10, 20, 50)

WEIGHTS = {
    "social_added": 3.0,
    "social_removed": -5.0,
    "website_down": -4.0,
    "website_back": 2.0,
    "description_updated": 1.0,
    "liquidity_up": 3.0,
    "liquidity_drop": -6.0,
    "holders_milestone": 2.0,
    "telegram_growth": 2.0,
    "mcap_multiple": 3.0,
    "new_high": 1.0,
    "drawdown": -5.0,
    "boost": 1.0,
    "cto": 1.0,
    "risk_new": -6.0,
    "risk_cleared": 2.0,
    "authority_enabled": -15.0,
    "rugged": -50.0,
}


@dataclass
class Event:
    kind: str
    message: str
    weight: float

    def to_list(self, ts: str) -> list:
        return [ts, self.kind, self.message, self.weight]


def _ev(kind: str, message: str) -> Event:
    return Event(kind, message, WEIGHTS[kind])


def detect(prev: Snapshot, cur: Snapshot, *, first_mcap: float, peak_price: float, drawdown_alert_pct: float = 50) -> list[Event]:
    events: list[Event] = []

    # Socials added / removed
    before, after = set(prev.socials.present()), set(cur.socials.present())
    for kind in sorted(after - before):
        events.append(_ev("social_added", f"new {kind} link: {getattr(cur.socials, kind)}"))
    for kind in sorted(before - after):
        events.append(_ev("social_removed", f"{kind} link removed"))

    # Website liveness
    if prev.website and cur.website:
        if prev.website.reachable and not cur.website.reachable:
            events.append(_ev("website_down", f"website went down ({cur.website.error or cur.website.status})"))
        elif not prev.website.reachable and cur.website.reachable:
            events.append(_ev("website_back", "website is reachable again"))

    if cur.description and cur.description.strip() != (prev.description or "").strip():
        events.append(_ev("description_updated", "project description updated"))

    # Liquidity moves
    if prev.liquidity_usd > 0:
        change = (cur.liquidity_usd - prev.liquidity_usd) / prev.liquidity_usd
        if change >= 0.5:
            events.append(_ev("liquidity_up", f"liquidity +{change:.0%} to ${cur.liquidity_usd:,.0f}"))
        elif change <= -0.4:
            events.append(_ev("liquidity_drop", f"liquidity {change:.0%} to ${cur.liquidity_usd:,.0f}"))

    # Holder milestones
    ph, ch = prev.safety.holders or 0, cur.safety.holders or 0
    crossed = [m for m in HOLDER_MILESTONES if ph < m <= ch]
    if crossed:
        events.append(_ev("holders_milestone", f"holders crossed {crossed[-1]:,} (now {ch:,})"))

    # Telegram community growth
    pt, ct = prev.telegram_members or 0, cur.telegram_members or 0
    if pt >= 50 and ct >= pt * 1.5:
        events.append(_ev("telegram_growth", f"telegram grew {pt:,} -> {ct:,} members"))

    # Market cap multiples vs first sighting
    if first_mcap > 0:
        hit = [m for m in MCAP_MULTIPLES if prev.market_cap < first_mcap * m <= cur.market_cap]
        if hit:
            events.append(_ev("mcap_multiple", f"market cap {hit[-1]}x since first seen (${cur.market_cap:,.0f})"))

    # Price highs / drawdowns relative to the tracked peak
    if peak_price > 0 and cur.price_usd > peak_price * 1.2:
        events.append(_ev("new_high", f"new high ${cur.price_usd:.8g} (+{cur.price_usd / peak_price - 1:.0%} over prior peak)"))
    if peak_price > 0 and cur.price_usd > 0:
        prev_dd = 1 - prev.price_usd / peak_price if prev.price_usd else 0
        cur_dd = 1 - cur.price_usd / peak_price
        if cur_dd * 100 >= drawdown_alert_pct > prev_dd * 100:
            events.append(_ev("drawdown", f"down {cur_dd:.0%} from tracked high"))

    if cur.boosts_active > prev.boosts_active:
        events.append(_ev("boost", f"DexScreener boost active ({cur.boosts_active})"))
    if cur.cto and not prev.cto:
        events.append(_ev("cto", "community takeover registered"))

    # Safety changes
    prev_risks, cur_risks = set(prev.safety.danger_risks), set(cur.safety.danger_risks)
    for risk in sorted(cur_risks - prev_risks):
        events.append(_ev("risk_new", f"new RugCheck danger: {risk}"))
    for risk in sorted(prev_risks - cur_risks):
        events.append(_ev("risk_cleared", f"RugCheck danger cleared: {risk}"))
    if prev.safety.mint_revoked and cur.safety.mint_revoked is False:
        events.append(_ev("authority_enabled", "mint authority re-enabled!"))
    if prev.safety.freeze_revoked and cur.safety.freeze_revoked is False:
        events.append(_ev("authority_enabled", "freeze authority re-enabled!"))
    if cur.safety.rugged and not prev.safety.rugged:
        events.append(_ev("rugged", "RugCheck now flags this token as rugged"))

    return events
