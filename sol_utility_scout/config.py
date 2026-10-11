"""Tunable parameters for the Solana utility scout.

Everything the discovery filter, the 100-point rating and the conviction
tracker use lives here, so retuning never needs a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RatingWeights:
    utility: int = 25  # does it describe/ship an actual product?
    socials: int = 20  # website, X, Telegram, Discord, GitHub, docs -- and are they alive?
    safety: int = 25  # mint/freeze revoked, LP locked/burned, holder spread, RugCheck risks
    market: int = 15  # liquidity depth and MC/liquidity sanity
    traction: int = 15  # holders, volume, buy pressure

    @property
    def total(self) -> int:
        return self.utility + self.socials + self.safety + self.market + self.traction


@dataclass(frozen=True)
class ScoutConfig:
    chain_id: str = "solana"

    # Discovery: tokens launched within the last 7 days are evaluated, so a
    # project that was quiet at launch but is building traction still gets found.
    max_launch_age_hours: float = 168
    # Watch every accepted token for this long after it was first picked up.
    tracking_days: float = 7
    # When a watch period ends, a token whose tech still reads as utility and
    # whose numbers improved over the period stays on the radar for another
    # period instead of being closed out. Improving means: conviction held or
    # rose and is at least conviction_medium, holders grew, and liquidity held
    # within this tolerance of where the period started.
    radar_liquidity_tolerance: float = 0.9

    # Flush: drop a watched token early when it stops being worth holding.
    # Checked from the flush_min_checks-th observation on, so one bad
    # reading right after launch doesn't kick a token out.
    flush_min_checks: int = 3
    flush_conviction_below: float = 55  # conviction fell into LOW
    flush_fade_from_peak: float = 10  # conviction this far under its peak...
    flush_fade_mcap_ratio: float = 0.5  # ...while market cap is under this share of first seen
    flush_liquidity_ratio: float = 0.6  # liquidity bled under this share of first seen

    # Hard filters (any of these -> rejected / dropped from the watchlist)
    liquidity_min_usd: float = 10_000
    require_mint_revoked: bool = True
    require_freeze_revoked: bool = True
    top10_holder_pct_max: float = 45  # excluding AMM pools / lockers
    rugcheck_danger_max: int = 2  # more "danger" level risks than this -> reject
    drawdown_kill_pct: float = 85  # price fell this far from the tracked high -> dead

    # Market sweet spots
    liquidity_good_usd: float = 50_000
    mcap_to_liquidity_max: float = 12
    holders_good: int = 1_000
    volume_h24_good_usd: float = 250_000

    # Watchlist entry threshold on the snapshot rating (0-100)
    watch_rating_min: float = 55
    # ...and the utility component alone must clear this share of its weight,
    # otherwise a well-run meme with great traction would fill the watchlist.
    utility_min_ratio: float = 0.4
    # "Real tech" gate: at least this many proofs that a product exists --
    # a GitHub link, a docs/whitepaper link, or a link to a working app.
    # Keywords alone are cheap to write; these take actual work to fake.
    tech_proof_min: int = 1
    # Labels on the conviction score
    conviction_high: float = 75
    conviction_medium: float = 55

    # Conviction dynamics: per-update move is capped so one noisy snapshot
    # can't swing the score, conviction has to be earned over several checks.
    conviction_max_step: float = 8

    weights: RatingWeights = RatingWeights()


DEFAULT_CONFIG = ScoutConfig()

DEFAULT_STORE_PATH = os.environ.get("SCOUT_STORE_PATH", ".sol_utility_scout.json")
