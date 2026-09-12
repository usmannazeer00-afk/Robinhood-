"""Detects the 'launch -> pump -> retracement -> higher low' chart shape.

This is the entry pattern from the spec: a token that already ripped
+300% on one candle (pure FOMO) scores worse here than one that pumped,
gave back 20-40% of the move, and is basing above its launch price with
volume starting to pick back up.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import PricePoint


@dataclass
class StructureResult:
    has_higher_low: bool
    retracement_pct: float | None
    notes: str


def detect_higher_low(
    price_history: list[PricePoint],
    retracement_min_pct: float = 20.0,
    retracement_max_pct: float = 40.0,
) -> StructureResult:
    if len(price_history) < 3:
        return StructureResult(False, None, "not enough price history yet")

    prices = [p.price_usd for p in price_history]
    launch_price = prices[0]

    peak_idx = max(range(len(prices)), key=lambda i: prices[i])
    peak_price = prices[peak_idx]

    if peak_price <= launch_price:
        return StructureResult(False, None, "no initial pump above launch price")

    # Look for the lowest point after the peak (the retracement low).
    post_peak = prices[peak_idx:]
    if len(post_peak) < 2:
        return StructureResult(False, None, "pump still forming, no retracement yet")

    trough_offset = min(range(len(post_peak)), key=lambda i: post_peak[i])
    trough_idx = peak_idx + trough_offset
    trough_price = prices[trough_idx]

    retracement_pct = (peak_price - trough_price) / peak_price * 100

    if trough_price <= launch_price:
        return StructureResult(
            False, retracement_pct, "retracement gave back the entire pump (lower low, not higher low)"
        )

    if not (retracement_min_pct <= retracement_pct <= retracement_max_pct):
        note = (
            "retracement too shallow (likely still FOMO-extended)"
            if retracement_pct < retracement_min_pct
            else "retracement too deep (structure looks broken)"
        )
        return StructureResult(False, retracement_pct, note)

    current_price = prices[-1]
    if current_price < trough_price:
        return StructureResult(False, retracement_pct, "price has broken back below the higher low")

    return StructureResult(
        True,
        retracement_pct,
        f"higher low confirmed at {retracement_pct:.0f}% retracement, holding above launch price",
    )
