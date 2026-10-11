"""Conviction: a score that has to be earned over time.

A token enters the watchlist with conviction equal to its first rating.
Every later check moves it by a bounded step built from:

* how the snapshot rating moved since the last check,
* the events that happened in between (weighted, see events.WEIGHTS),
* holder growth since the last check,
* a survival bonus for each full day it stays alive after launch,
* price structure: a higher low across the recent samples is a bonus,
  a lower high a penalty.

The step cap (config.conviction_max_step) means conviction can't jump on
one noisy reading -- a project that keeps delivering for days climbs
steadily, one that goes quiet or bleeds drifts down.
"""

from __future__ import annotations

from .config import ScoutConfig


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def structure_signal(prices: list[float]) -> float:
    """+1 for a higher-low structure over the last samples, -1 for lower highs, 0 otherwise."""
    pts = [p for p in prices if p > 0][-6:]
    if len(pts) < 4:
        return 0.0
    half = len(pts) // 2
    first, second = pts[:half], pts[half:]
    if min(second) > min(first) and second[-1] >= second[0] * 0.95:
        return 1.0
    if max(second) < max(first) and min(second) < min(first):
        return -1.0
    return 0.0


def step(
    *,
    rating_prev: float,
    rating_now: float,
    event_weights: list[float],
    holders_prev: int | None,
    holders_now: int | None,
    days_survived_new: int,
    prices: list[float],
    config: ScoutConfig,
) -> tuple[float, list[str]]:
    """Returns (bounded conviction delta, human-readable reasons)."""
    reasons: list[str] = []
    delta = 0.0

    rating_part = 0.4 * (rating_now - rating_prev)
    if abs(rating_part) >= 0.5:
        reasons.append(f"rating {rating_prev:.0f}->{rating_now:.0f}")
    delta += rating_part

    ev = sum(event_weights)
    if ev:
        reasons.append(f"events {ev:+.0f}")
    delta += ev

    if holders_prev and holders_now is not None:
        growth = (holders_now - holders_prev) / holders_prev
        part = 4 * _clamp(growth / 0.5, -1, 1)
        if abs(part) >= 0.5:
            reasons.append(f"holders {growth:+.0%}")
        delta += part

    if days_survived_new:
        delta += 2 * days_survived_new
        reasons.append(f"survived day +{days_survived_new}")

    s = structure_signal(prices)
    if s:
        delta += 2 * s
        reasons.append("higher lows" if s > 0 else "lower highs")

    cap = config.conviction_max_step
    return _clamp(delta, -cap, cap), reasons


def label(conviction: float, config: ScoutConfig) -> str:
    if conviction >= config.conviction_high:
        return "HIGH"
    if conviction >= config.conviction_medium:
        return "MEDIUM"
    return "LOW"
