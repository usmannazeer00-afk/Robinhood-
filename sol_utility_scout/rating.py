"""Point-in-time 100-point rating of one Snapshot.

| Factor   | Points | What earns it |
|----------|-------:|---------------|
| Utility  | 25 | describes a concrete use case + evidence a product exists, minus meme markers |
| Socials  | 20 | website (live, real content), X account, Telegram (by size), Discord, GitHub, docs |
| Safety   | 25 | mint + freeze revoked, LP locked/burned, holder spread, no RugCheck dangers/insiders |
| Market   | 15 | liquidity depth, sane MC/liquidity |
| Traction | 15 | holder count, 24h volume, buy pressure |

Hard filters force ``rejected=True`` (and the token is never watched, or
is dropped if already watched): rugged, active mint or freeze authority,
liquidity below the floor, top-10 concentration over the cap, too many
RugCheck "danger" risks.
"""

from __future__ import annotations

from .config import ScoutConfig
from .models import Rating, Snapshot
from .utility import utility_points


def _lin(value: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, clamped."""
    if hi <= lo:
        return 1.0 if value >= hi else 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def hard_filter_reasons(snap: Snapshot, config: ScoutConfig) -> list[str]:
    s = snap.safety
    reasons: list[str] = []
    if s.rugged:
        reasons.append("RugCheck flags it as rugged")
    if config.require_mint_revoked and s.mint_revoked is False:
        reasons.append("mint authority still active (supply can be inflated)")
    if config.require_freeze_revoked and s.freeze_revoked is False:
        reasons.append("freeze authority still active (wallets can be frozen)")
    if snap.liquidity_usd < config.liquidity_min_usd:
        reasons.append(f"liquidity ${snap.liquidity_usd:,.0f} < ${config.liquidity_min_usd:,.0f} floor")
    if s.top10_pct is not None and s.top10_pct > config.top10_holder_pct_max:
        reasons.append(f"top-10 holders own {s.top10_pct:.0f}% (> {config.top10_holder_pct_max:.0f}%)")
    if len(s.danger_risks) > config.rugcheck_danger_max:
        reasons.append("RugCheck dangers: " + ", ".join(s.danger_risks))
    return reasons


def rate(snap: Snapshot, config: ScoutConfig, website_text: str = "") -> Rating:
    w = config.weights
    notes: list[str] = []

    # --- Utility
    proof = tech_proof(snap)
    utility, category, unotes = utility_points(snap.description, f"{snap.name} {snap.symbol}", website_text, w.utility, proof)
    notes.extend(unotes)

    # --- Socials (out of 20 by default, scaled to the weight)
    so = snap.socials
    social_raw = 0.0  # out of 10
    site = snap.website
    if so.website:
        if site and site.reachable and site.text_length >= 400:
            social_raw += 3
            if site.has_docs_link or site.has_github_link or site.has_app_link:
                social_raw += 1
        elif site and site.reachable:
            social_raw += 1.5
            notes.append("website is thin (template/parked?)")
        else:
            notes.append("website unreachable")
    if so.twitter:
        social_raw += 2
    if so.telegram:
        social_raw += 0.5
        if snap.telegram_members:
            social_raw += 1.5 * _lin(snap.telegram_members, 50, 2_000)
            notes.append(f"telegram: {snap.telegram_members:,} members")
    if so.discord:
        social_raw += 0.5
    if so.github or (site and site.has_github_link):
        social_raw += 1
    if so.docs or (site and site.has_docs_link):
        social_raw += 0.5
    socials = min(10.0, social_raw) / 10 * w.socials
    if not so.present():
        notes.append("no socials at all")

    # --- Safety
    s = snap.safety
    safety_raw = 0.0  # out of 10
    if s.available:
        safety_raw += 2 if s.mint_revoked else 0
        safety_raw += 1.5 if s.freeze_revoked else 0
        if s.lp_locked_pct is not None:
            safety_raw += 2.5 * _lin(s.lp_locked_pct, 50, 100)
        if s.top10_pct is not None:
            safety_raw += 2.5 * (1 - _lin(s.top10_pct, 15, config.top10_holder_pct_max))
        safety_raw += 1.5 if not s.danger_risks else 0
        safety_raw -= min(len(s.warn_risks), 3) * 0.3
        if s.insiders_detected:
            safety_raw -= min(4.0, 0.5 + s.insiders_detected / 10)
            notes.append(f"{s.insiders_detected} insider wallets detected")
        if s.top10_pct is not None:
            notes.append(f"top10 {s.top10_pct:.0f}%, LP locked {s.lp_locked_pct or 0:.0f}%")
    else:
        safety_raw = 3  # unknown, not proven bad -- partial credit
        notes.append("RugCheck report unavailable")
    safety = max(0.0, min(10.0, safety_raw)) / 10 * w.safety

    # --- Market
    mc_liq = snap.market_cap / snap.liquidity_usd if snap.liquidity_usd else float("inf")
    market_raw = 6 * _lin(snap.liquidity_usd, config.liquidity_min_usd, config.liquidity_good_usd)
    market_raw += 4 * (1 - _lin(mc_liq, config.mcap_to_liquidity_max / 2, config.mcap_to_liquidity_max * 1.5))
    market = market_raw / 10 * w.market

    # --- Traction
    holders = s.holders or 0
    traction_raw = 4 * _lin(holders, 100, config.holders_good)
    traction_raw += 4 * _lin(snap.volume_h24, 10_000, config.volume_h24_good_usd)
    total_tx = snap.buys_h24 + snap.sells_h24
    if total_tx >= 50:
        traction_raw += 2 * _lin(snap.buys_h24 / total_tx, 0.45, 0.6)
    traction = traction_raw / 10 * w.traction

    notes.append("tech proof: " + (", ".join(proof) if proof else "none (no GitHub, docs, app, API or bot link)"))

    reasons = hard_filter_reasons(snap, config)
    total = round(utility + socials + safety + market + traction, 1)
    return Rating(
        total=0.0 if reasons else total,
        utility=round(utility, 1),
        socials=round(socials, 1),
        safety=round(safety, 1),
        market=round(market, 1),
        traction=round(traction, 1),
        category=category,
        rejected=bool(reasons),
        reject_reasons=reasons,
        notes=notes,
        tech_proof=proof,
    )


def tech_proof(snap: Snapshot) -> list[str]:
    """Evidence an actual product exists, beyond what the description claims."""
    site, so = snap.website, snap.socials
    proof = []
    if so.github or (site and site.has_github_link):
        proof.append("github")
    if so.docs or (site and site.has_docs_link):
        proof.append("docs")
    if site and site.reachable and site.has_app_link:
        proof.append("app")
    if site and site.reachable and site.has_api_link:
        proof.append("api")
    if site and site.reachable and site.has_bot_link:
        proof.append("telegram bot")
    return proof
