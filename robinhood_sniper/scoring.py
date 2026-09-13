"""The 100-point scoring rubric from the strategy spec.

    Factor                      Points
    Age 5-45m                    10
    $75K-$400K MC                10
    $40K+ liquidity              15
    MC/Liq <8x                    5
    Accelerating 5m volume        20
    Buy/sell >= 1.5               10
    Increasing unique buyers      10
    Higher-low structure          10
    Healthy holder distribution    5
    Clean deployer/LP              5
    Total                       100

    Score 80-100 -> potential sniper entry (green)
    Score 65-79  -> watch (yellow)
    Score <65    -> ignore (red)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .config import SniperConfig, DEFAULT_CONFIG
from .models import TokenSnapshot
from .structure import StructureResult, detect_higher_low


class Verdict(str, Enum):
    ENTRY = "ENTRY"
    WATCH = "WATCH"
    IGNORE = "IGNORE"

    @property
    def emoji(self) -> str:
        return {"ENTRY": "🟢", "WATCH": "🟡", "IGNORE": "🔴"}[self.value]


@dataclass
class FactorScore:
    name: str
    points: float
    max_points: float
    note: str = ""


@dataclass
class ScoreResult:
    total: float
    max_total: int
    verdict: Verdict
    factors: list[FactorScore] = field(default_factory=list)
    structure: StructureResult | None = None
    hard_filter_failed: str | None = None

    @property
    def passed_hard_filters(self) -> bool:
        return self.hard_filter_failed is None


def _volume_acceleration_score(token: TokenSnapshot, max_points: int) -> FactorScore:
    """Rewards volume that is intensifying, not just large.

    Two independent signals feed this, each worth half the bucket:
      * per-minute rate rising as the window shortens (1m rate >= 5m rate
        >= 15m rate implies the last minute is hotter than the last 15),
      * consecutive 5-minute buckets trending upward, e.g. 8k -> 18k -> 35k.
    """
    half = max_points / 2
    score = 0.0
    notes = []

    rate_1m = token.volume_1m
    rate_5m = token.volume_5m / 5 if token.volume_5m else 0.0
    rate_15m = token.volume_15m / 15 if token.volume_15m else 0.0
    if rate_1m and rate_5m and rate_15m and rate_1m >= rate_5m >= rate_15m:
        score += half
        notes.append("per-minute rate accelerating (1m >= 5m avg >= 15m avg)")
    else:
        notes.append("per-minute rate not clearly accelerating")

    buckets = token.volume_5m_buckets
    if len(buckets) >= 2 and all(b > 0 for b in buckets) and all(
        buckets[i] < buckets[i + 1] for i in range(len(buckets) - 1)
    ):
        score += half
        notes.append(f"5m buckets rising ({' -> '.join(f'{b:,.0f}' for b in buckets)})")
    else:
        notes.append("5m buckets not monotonically rising")

    return FactorScore("Volume acceleration", score, max_points, "; ".join(notes))


def _buyer_acceleration_score(token: TokenSnapshot, max_points: int) -> FactorScore:
    series = token.unique_buyers_series
    if len(series) < 3:
        return FactorScore("Unique buyer acceleration", 0, max_points, "not enough buyer history yet")

    strictly_increasing = all(series[i] < series[i + 1] for i in range(len(series) - 1))
    if not strictly_increasing:
        return FactorScore(
            "Unique buyer acceleration", 0, max_points,
            f"buyer count flat/declining ({' -> '.join(map(str, series))}) — possible bot/wash activity",
        )

    deltas = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    accelerating = all(deltas[i] <= deltas[i + 1] for i in range(len(deltas) - 1))
    growth_ratio = series[-1] / series[0] if series[0] else float("inf")

    if accelerating and growth_ratio >= 1.5:
        return FactorScore(
            "Unique buyer acceleration", max_points, max_points,
            f"buyers accelerating ({' -> '.join(map(str, series))})",
        )
    if growth_ratio >= 1.3:
        return FactorScore(
            "Unique buyer acceleration", max_points * 0.6, max_points,
            f"buyers rising but not clearly accelerating ({' -> '.join(map(str, series))})",
        )
    return FactorScore(
        "Unique buyer acceleration", max_points * 0.3, max_points,
        f"buyers rising slowly ({' -> '.join(map(str, series))}) — watch for wash trading",
    )


def score_token(token: TokenSnapshot, config: SniperConfig = DEFAULT_CONFIG) -> ScoreResult:
    w = config.weights
    factors: list[FactorScore] = []

    # --- Hard filters (fail => score is thrown out regardless of points) ---
    if token.chain != config.chain:
        return ScoreResult(0, w.total, Verdict.IGNORE, [], None, f"wrong chain: {token.chain}")

    mcap_to_liq = token.mcap_to_liquidity
    if token.liquidity.usd and token.liquidity.usd < 5_000:
        return ScoreResult(0, w.total, Verdict.IGNORE, [], None, "liquidity too thin to be tradable at all")

    first_seen = token.token_first_pool_created_at
    if first_seen is not None:
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        first_pool_age = (datetime.now(timezone.utc) - first_seen).total_seconds() / 60.0
        if first_pool_age > config.age_max_minutes:
            return ScoreResult(
                0, w.total, Verdict.IGNORE, [], None,
                f"not a fresh listing — token already has a pool from {first_pool_age:.0f}m ago (established token, not a new launch)",
            )

    if token.deployer.address and not token.deployer.is_clean:
        reasons = []
        d = token.deployer
        if d.owns_large_pct:
            reasons.append("deployer owns a large % of supply")
        if d.has_sold:
            reasons.append("deployer has already sold")
        if d.created_multiple_failed_tokens:
            reasons.append("deployer has multiple failed tokens")
        if d.linked_to_launch_bundles:
            reasons.append("deployer linked to launch bundles")
        if d.sends_to_fresh_wallets_before_selling:
            reasons.append("deployer routes to fresh wallets before selling")
        return ScoreResult(0, w.total, Verdict.IGNORE, [], None, "; ".join(reasons))

    # --- 1. Age ---
    age = token.age_minutes
    if config.age_min_minutes <= age <= config.age_max_minutes:
        pts = w.age if config.age_ideal_min <= age <= config.age_ideal_max else w.age * 0.6
        factors.append(FactorScore("Age", pts, w.age, f"{age:.0f} min old"))
    else:
        factors.append(FactorScore("Age", 0, w.age, f"{age:.0f} min old — outside {config.age_min_minutes:.0f}-{config.age_max_minutes:.0f}m window"))

    # --- 2. Market cap ---
    mc = token.market_cap_usd
    if config.mcap_min <= mc <= config.mcap_max:
        pts = w.market_cap if config.mcap_sweet_min <= mc <= config.mcap_sweet_max else w.market_cap * 0.6
        factors.append(FactorScore("Market cap", pts, w.market_cap, f"${mc:,.0f}"))
    else:
        factors.append(FactorScore("Market cap", 0, w.market_cap, f"${mc:,.0f} — outside range"))

    # --- 3. Liquidity ---
    liq = token.liquidity.usd
    if liq >= config.liquidity_min:
        pts = w.liquidity if config.liquidity_preferred_min <= liq else w.liquidity * 0.6
        factors.append(FactorScore("Liquidity", pts, w.liquidity, f"${liq:,.0f}"))
    else:
        factors.append(FactorScore("Liquidity", 0, w.liquidity, f"${liq:,.0f} — below ${config.liquidity_min:,.0f} floor"))

    # --- 4. MC/Liquidity ratio ---
    if mcap_to_liq is not None and mcap_to_liq <= config.mcap_to_liquidity_max:
        factors.append(FactorScore("MC/Liquidity", w.mcap_to_liquidity, w.mcap_to_liquidity, f"{mcap_to_liq:.1f}x"))
    else:
        shown = f"{mcap_to_liq:.1f}x" if mcap_to_liq is not None else "n/a"
        factors.append(FactorScore("MC/Liquidity", 0, w.mcap_to_liquidity, f"{shown} — over {config.mcap_to_liquidity_max:.0f}x"))

    # --- 5. Volume acceleration ---
    factors.append(_volume_acceleration_score(token, w.volume_acceleration))

    # --- 6. Buy/sell ratio ---
    ratio = token.buy_sell_ratio
    if ratio is not None and ratio >= config.buy_sell_ratio_min:
        pts = w.buy_sell_ratio if config.buy_sell_ratio_preferred_min <= ratio <= config.buy_sell_ratio_preferred_max else w.buy_sell_ratio * 0.7
        factors.append(FactorScore("Buy/sell ratio", pts, w.buy_sell_ratio, f"{ratio:.2f}x"))
    else:
        shown = f"{ratio:.2f}x" if ratio is not None else "n/a"
        factors.append(FactorScore("Buy/sell ratio", 0, w.buy_sell_ratio, f"{shown} — below {config.buy_sell_ratio_min:.1f}x"))

    # --- 7. Unique buyer acceleration ---
    factors.append(_buyer_acceleration_score(token, w.buyer_acceleration))

    # --- 8. Price structure (higher low) ---
    structure = detect_higher_low(token.price_history, config.retracement_min_pct, config.retracement_max_pct)
    factors.append(FactorScore("Higher-low structure", w.price_structure if structure.has_higher_low else 0, w.price_structure, structure.notes))

    # --- 9. Holder distribution ---
    t10 = token.top10_holder_pct
    if t10 is None:
        factors.append(FactorScore("Holder distribution", w.holder_distribution * 0.4, w.holder_distribution, "holder data unavailable"))
    elif t10 <= config.top10_holder_pct_good:
        factors.append(FactorScore("Holder distribution", w.holder_distribution, w.holder_distribution, f"top10 = {t10:.0f}%"))
    elif t10 <= config.top10_holder_pct_max:
        factors.append(FactorScore("Holder distribution", w.holder_distribution * 0.5, w.holder_distribution, f"top10 = {t10:.0f}% (borderline)"))
    else:
        factors.append(FactorScore("Holder distribution", 0, w.holder_distribution, f"top10 = {t10:.0f}% — too concentrated"))

    # --- 10. Clean deployer / healthy LP ---
    lp_ok = token.liquidity.is_healthy
    deployer_ok = token.deployer.is_clean
    if deployer_ok and lp_ok:
        factors.append(FactorScore("Deployer/LP", w.deployer_lp, w.deployer_lp, "deployer clean, LP locked/burned or safely controlled"))
    elif deployer_ok or lp_ok:
        factors.append(FactorScore("Deployer/LP", w.deployer_lp * 0.5, w.deployer_lp, "one of deployer/LP checks is unresolved"))
    else:
        factors.append(FactorScore("Deployer/LP", 0, w.deployer_lp, "LP not locked/burned and freely removable"))

    total = sum(f.points for f in factors)
    if total >= config.entry_score_min:
        verdict = Verdict.ENTRY
    elif total >= config.watch_score_min:
        verdict = Verdict.WATCH
    else:
        verdict = Verdict.IGNORE

    return ScoreResult(total, w.total, verdict, factors, structure, None)
