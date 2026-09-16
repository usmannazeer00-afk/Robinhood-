"""The 100-point rubric for a 15m Binance USDT-M futures short setup.

    Factor                            Points
    Higher-timeframe trend              15
    15m swing structure (LH/LL)         15
    Resistance rejection candle         15
    Momentum turning down               15
    Volume / order-flow                 15
    Positioning (funding + OI)          15
    Risk/reward (ATR-based)             10
    Total                              100

    Score >=75 -> SHORT (green), 60-74 -> WATCH (yellow), <60 -> AVOID (red)

Design intent: this rewards a *pullback-into-resistance* short inside an
already-confirmed downtrend over blind top-picking. The higher-timeframe
filter keeps it from fighting a strong uptrend; the 15m structure and
resistance-rejection factors want to see the market already making lower
highs/lower lows before a rejection candle counts for much; momentum and
order-flow want confirmation, not just a hunch; and positioning uses
funding/open-interest as a contrarian crowding signal, not a primary
trigger. Risk/reward is graded last, on the trade idea the other factors
produced, so a technically "good" setup with no room to the next support
still loses points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import DEFAULT_CONFIG, ShortScannerConfig
from .indicators import (
    SwingPoint,
    atr,
    bearish_rsi_divergence,
    bollinger_bands,
    ema,
    is_bearish_engulfing,
    is_shooting_star,
    macd,
    rsi,
    swing_highs,
    swing_lows,
)
from .models import Candle, FuturesSnapshot

_MIN_15M_CANDLES = 60


class Verdict(str, Enum):
    SHORT = "SHORT"
    WATCH = "WATCH"
    AVOID = "AVOID"

    @property
    def emoji(self) -> str:
        return {"SHORT": "🟢", "WATCH": "🟡", "AVOID": "🔴"}[self.value]


@dataclass
class FactorScore:
    name: str
    points: float
    max_points: float
    note: str = ""


@dataclass
class TradeLevels:
    entry: float
    stop: float
    target: float
    risk_reward: float | None


@dataclass
class ShortScoreResult:
    total: float
    max_total: int
    verdict: Verdict
    factors: list[FactorScore] = field(default_factory=list)
    trade_levels: TradeLevels | None = None
    hard_filter_failed: str | None = None

    @property
    def passed_hard_filters(self) -> bool:
        return self.hard_filter_failed is None


def _htf_trend_score(snapshot: FuturesSnapshot, config: ShortScannerConfig, max_points: float) -> FactorScore:
    closes = [c.close for c in snapshot.candles_htf]
    ema_fast = ema(closes, config.ema_fast)[-1]
    ema_slow = ema(closes, config.ema_slow)[-1]
    if ema_fast is None or ema_slow is None:
        return FactorScore("HTF trend", 0, max_points, "not enough higher-timeframe history")

    price = closes[-1]
    if price < ema_slow and price < ema_fast and ema_fast < ema_slow:
        return FactorScore(
            "HTF trend", max_points, max_points,
            f"price below EMA{config.ema_fast}/EMA{config.ema_slow} on {config.htf_interval}, EMAs bearish-stacked",
        )
    if price < ema_slow:
        return FactorScore(
            "HTF trend", max_points * 0.6, max_points,
            f"price below EMA{config.ema_slow} on {config.htf_interval} but trend mixed",
        )
    return FactorScore(
        "HTF trend", 0, max_points,
        f"price above EMA{config.ema_slow} on {config.htf_interval} -- avoid shorting into the trend",
    )


def _ltf_structure_score(highs: list[SwingPoint], lows: list[SwingPoint], max_points: float) -> FactorScore:
    if len(highs) < 2 or len(lows) < 2:
        return FactorScore("15m structure", 0, max_points, "not enough swing points yet")

    lower_highs = highs[-1].price < highs[-2].price
    lower_lows = lows[-1].price < lows[-2].price
    if lower_highs and lower_lows:
        return FactorScore("15m structure", max_points, max_points, "lower highs and lower lows -- confirmed downtrend")
    if lower_highs:
        return FactorScore("15m structure", max_points * 0.6, max_points, "lower highs but lows not confirmed yet")
    return FactorScore("15m structure", 0, max_points, "no lower-high structure on 15m")


def _resistance_level(highs: list[SwingPoint], bb_upper: list[float | None]) -> float | None:
    candidates = []
    if highs:
        candidates.append(highs[-1].price)
    if bb_upper and bb_upper[-1] is not None:
        candidates.append(bb_upper[-1])
    return max(candidates) if candidates else None


def _resistance_rejection_score(
    candles: list[Candle], resistance: float | None, atr_series: list[float | None], max_points: float
) -> FactorScore:
    if resistance is None or not atr_series or atr_series[-1] is None:
        return FactorScore("Resistance rejection", 0, max_points, "no resistance level to test yet")

    tolerance = atr_series[-1] * 0.35
    recent = candles[-3:]
    touched = any(c.high >= resistance - tolerance for c in recent)
    if not touched:
        return FactorScore("Resistance rejection", 0, max_points, f"price not near resistance (~{resistance:.6g})")

    last, prev = candles[-1], candles[-2]
    reversal = (
        is_shooting_star(last)
        or is_bearish_engulfing(prev, last)
        or (last.high >= resistance - tolerance and last.close < last.open)
    )
    if reversal:
        return FactorScore("Resistance rejection", max_points, max_points, f"rejection candle at resistance (~{resistance:.6g})")
    return FactorScore(
        "Resistance rejection", max_points * 0.4, max_points,
        f"testing resistance (~{resistance:.6g}) but no confirmed reversal candle yet",
    )


def _momentum_score(
    closes: list[float],
    rsi_series: list[float | None],
    macd_line: list[float | None],
    signal_line: list[float | None],
    highs: list[SwingPoint],
    config: ShortScannerConfig,
    max_points: float,
) -> FactorScore:
    half = max_points / 2
    score = 0.0
    notes = []

    recent_rsi = [r for r in rsi_series[-8:] if r is not None]
    current_rsi = rsi_series[-1]
    if recent_rsi and current_rsi is not None:
        peak_rsi = max(recent_rsi)
        if peak_rsi >= config.rsi_overbought and current_rsi <= peak_rsi - 5:
            score += half
            notes.append(f"RSI rolled over from {peak_rsi:.0f} to {current_rsi:.0f}")
        else:
            notes.append(f"RSI {current_rsi:.0f} not yet rolling over from overbought")
    else:
        notes.append("RSI not available yet")

    divergence = bearish_rsi_divergence(closes, rsi_series, highs)
    macd_cross_down = (
        len(macd_line) >= 2
        and macd_line[-2] is not None
        and signal_line[-2] is not None
        and macd_line[-1] is not None
        and signal_line[-1] is not None
        and macd_line[-2] >= signal_line[-2]
        and macd_line[-1] < signal_line[-1]
    )
    if divergence or macd_cross_down:
        score += half
        notes.append("bearish RSI divergence" if divergence else "MACD bearish crossover")
    else:
        notes.append("no divergence or MACD cross down yet")

    return FactorScore("Momentum", score, max_points, "; ".join(notes))


def _volume_orderflow_score(candles: list[Candle], max_points: float) -> FactorScore:
    if len(candles) < 21:
        return FactorScore("Volume / order-flow", 0, max_points, "not enough volume history")

    avg_volume = sum(c.volume for c in candles[-21:-1]) / 20
    half = max_points / 2
    score = 0.0
    notes = []

    last = candles[-1]
    if last.close < last.open and avg_volume and last.volume >= avg_volume * 1.3:
        score += half
        notes.append(f"bearish candle on {last.volume / avg_volume:.1f}x average volume")
    else:
        notes.append("no above-average bearish volume yet")

    recent = candles[-3:]
    sell_ratios = [c.taker_sell_base_volume / c.volume for c in recent if c.volume]
    if sell_ratios and (sum(sell_ratios) / len(sell_ratios)) > 0.55:
        score += half
        notes.append("taker sell volume dominating recent candles")
    else:
        notes.append("taker flow not clearly sell-dominated")

    return FactorScore("Volume / order-flow", score, max_points, "; ".join(notes))


def _positioning_score(snapshot: FuturesSnapshot, config: ShortScannerConfig, max_points: float) -> FactorScore:
    funding_pts_max = max_points * (8 / 15)
    oi_pts_max = max_points - funding_pts_max
    score = 0.0
    notes = []

    if snapshot.funding_rate is None:
        notes.append("funding rate unavailable")
    elif snapshot.funding_rate >= config.funding_rate_hot:
        score += funding_pts_max
        notes.append(f"funding {snapshot.funding_rate * 100:.3f}% -- longs paying a premium (crowded)")
    elif snapshot.funding_rate >= 0:
        score += funding_pts_max * 0.5
        notes.append(f"funding {snapshot.funding_rate * 100:.3f}% -- mildly long-skewed")
    else:
        notes.append(f"funding {snapshot.funding_rate * 100:.3f}% -- shorts paying, not crowded-long")

    oi_change = snapshot.open_interest_change_pct
    price_change = None
    if len(snapshot.candles_htf) >= 2:
        price_change = snapshot.candles_htf[-1].close - snapshot.candles_htf[-2].close

    if oi_change is not None:
        if oi_change > 2 and price_change is not None and price_change > 0:
            score += oi_pts_max
            notes.append(f"open interest +{oi_change:.1f}% into the recent rally -- fresh longs added")
        elif oi_change > 0:
            score += oi_pts_max * 0.4
            notes.append(f"open interest +{oi_change:.1f}%, no clear rally correlation")
        else:
            notes.append(f"open interest {oi_change:.1f}% -- not building")
    else:
        notes.append("open interest history unavailable")

    return FactorScore("Positioning (funding + OI)", score, max_points, "; ".join(notes))


def _build_trade_levels(
    candles: list[Candle],
    resistance: float | None,
    atr_series: list[float | None],
    lows: list[SwingPoint],
) -> TradeLevels | None:
    if not candles or not atr_series or atr_series[-1] is None:
        return None

    entry = candles[-1].close
    current_atr = atr_series[-1]
    stop_base = resistance if resistance is not None else entry + 1.5 * current_atr
    stop = max(stop_base + 0.5 * current_atr, entry + 0.5 * current_atr)

    support = lows[-1].price if lows else None
    target = support if support is not None and support < entry else entry - 2 * current_atr

    risk = stop - entry
    reward = entry - target
    rr = reward / risk if risk > 0 else None
    return TradeLevels(entry=entry, stop=stop, target=target, risk_reward=rr)


def _risk_reward_score(levels: TradeLevels | None, max_points: float) -> FactorScore:
    if levels is None or levels.risk_reward is None:
        return FactorScore("Risk/reward", 0, max_points, "could not compute a valid trade idea")

    rr = levels.risk_reward
    if rr >= 2.5:
        pts = max_points
    elif rr >= 2.0:
        pts = max_points * 0.7
    elif rr >= 1.5:
        pts = max_points * 0.4
    else:
        pts = 0
    return FactorScore(
        "Risk/reward", pts, max_points,
        f"entry {levels.entry:.6g} / stop {levels.stop:.6g} / target {levels.target:.6g} -- {rr:.1f}:1",
    )


def score_short_setup(snapshot: FuturesSnapshot, config: ShortScannerConfig = DEFAULT_CONFIG) -> ShortScoreResult:
    w = config.weights

    if len(snapshot.candles_15m) < _MIN_15M_CANDLES:
        return ShortScoreResult(0, w.total, Verdict.AVOID, [], None, "insufficient 15m candle history")
    if len(snapshot.candles_htf) < config.ema_slow + 5:
        return ShortScoreResult(0, w.total, Verdict.AVOID, [], None, "insufficient higher-timeframe candle history")
    if snapshot.quote_volume_24h < config.min_quote_volume_24h:
        return ShortScoreResult(
            0, w.total, Verdict.AVOID, [], None,
            f"24h quote volume ${snapshot.quote_volume_24h:,.0f} below ${config.min_quote_volume_24h:,.0f} liquidity floor",
        )

    candles = snapshot.candles_15m
    closes = [c.close for c in candles]

    rsi_series = rsi(closes, config.rsi_period)
    macd_line, signal_line, _hist = macd(closes, config.macd_fast, config.macd_slow, config.macd_signal)
    bb_upper, _bb_mid, _bb_lower = bollinger_bands(closes, config.bb_period, config.bb_std)
    atr_series = atr(candles, config.atr_period)
    highs = swing_highs(candles, config.swing_left, config.swing_right)
    lows = swing_lows(candles, config.swing_left, config.swing_right)
    resistance = _resistance_level(highs, bb_upper)

    factors = [
        _htf_trend_score(snapshot, config, w.htf_trend),
        _ltf_structure_score(highs, lows, w.ltf_structure),
        _resistance_rejection_score(candles, resistance, atr_series, w.resistance_rejection),
        _momentum_score(closes, rsi_series, macd_line, signal_line, highs, config, w.momentum),
        _volume_orderflow_score(candles, w.volume_orderflow),
        _positioning_score(snapshot, config, w.positioning),
    ]

    levels = _build_trade_levels(candles, resistance, atr_series, lows)
    factors.append(_risk_reward_score(levels, w.risk_reward))

    total = sum(f.points for f in factors)
    if total >= config.entry_score_min:
        verdict = Verdict.SHORT
    elif total >= config.watch_score_min:
        verdict = Verdict.WATCH
    else:
        verdict = Verdict.AVOID

    return ShortScoreResult(total, w.total, verdict, factors, levels, None)
