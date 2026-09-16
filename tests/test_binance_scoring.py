from dataclasses import replace

from binance_shorts.config import DEFAULT_CONFIG
from binance_shorts.providers.mock import MockFuturesProvider
from binance_shorts.scoring import Verdict, score_short_setup


def _snapshot(symbol: str):
    snapshots = {s.symbol: s for s in MockFuturesProvider().fetch_candidates(DEFAULT_CONFIG)}
    return snapshots[symbol]


def test_clean_downtrend_pullback_scores_as_short():
    result = score_short_setup(_snapshot("SHORTMEUSDT"))
    assert result.verdict == Verdict.SHORT
    assert result.total >= DEFAULT_CONFIG.entry_score_min
    assert result.hard_filter_failed is None


def test_clean_setup_fires_every_key_factor():
    result = score_short_setup(_snapshot("SHORTMEUSDT"))
    by_name = {f.name: f for f in result.factors}
    assert by_name["HTF trend"].points == by_name["HTF trend"].max_points
    assert by_name["15m structure"].points == by_name["15m structure"].max_points
    assert by_name["Resistance rejection"].points == by_name["Resistance rejection"].max_points
    assert by_name["Volume / order-flow"].points == by_name["Volume / order-flow"].max_points
    assert by_name["Positioning (funding + OI)"].points == by_name["Positioning (funding + OI)"].max_points
    assert by_name["Risk/reward"].points == by_name["Risk/reward"].max_points


def test_clean_setup_produces_a_short_trade_idea_below_current_price():
    result = score_short_setup(_snapshot("SHORTMEUSDT"))
    levels = result.trade_levels
    assert levels is not None
    assert levels.stop > levels.entry > levels.target
    assert levels.risk_reward is not None and levels.risk_reward >= 2.0


def test_strong_uptrend_is_never_a_short_even_if_other_factors_would_pass():
    result = score_short_setup(_snapshot("MOONUSDT"))
    htf = next(f for f in result.factors if f.name == "HTF trend")
    assert htf.points == 0
    assert result.verdict != Verdict.SHORT


def test_score_never_exceeds_max_total():
    for symbol in ("SHORTMEUSDT", "MOONUSDT", "CHOPPYUSDT"):
        result = score_short_setup(_snapshot(symbol))
        assert result.max_total == 100
        assert result.total <= 100


def test_thin_liquidity_is_a_hard_filter():
    result = score_short_setup(_snapshot("TOOTHINUSDT"))
    assert result.verdict == Verdict.AVOID
    assert result.total == 0
    assert result.hard_filter_failed is not None


def test_low_quote_volume_is_a_hard_filter_even_with_otherwise_sufficient_history():
    snapshot = replace(_snapshot("SHORTMEUSDT"), quote_volume_24h=1_000_000)
    result = score_short_setup(snapshot)
    assert result.verdict == Verdict.AVOID
    assert result.total == 0
    assert "liquidity floor" in result.hard_filter_failed


def test_hard_filter_failure_reports_zero_score_and_no_factors():
    result = score_short_setup(_snapshot("TOOTHINUSDT"))
    assert result.factors == []
    assert result.trade_levels is None
