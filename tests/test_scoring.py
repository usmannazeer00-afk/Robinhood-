from datetime import datetime, timedelta, timezone

from robinhood_sniper.config import DEFAULT_CONFIG
from robinhood_sniper.models import DeployerInfo, LiquidityInfo, PricePoint, TokenSnapshot
from robinhood_sniper.providers.mock import MockProvider
from robinhood_sniper.scoring import Verdict, score_token


def _base_token(**overrides) -> TokenSnapshot:
    now = datetime.now(timezone.utc)
    defaults = dict(
        chain=DEFAULT_CONFIG.chain,
        pair_address="0xpair",
        token_address="0xtoken",
        symbol="TEST",
        name="Test Token",
        created_at=now - timedelta(minutes=15),
        market_cap_usd=150_000,
        liquidity=LiquidityInfo(usd=50_000, locked_or_burned=True),
        volume_1m=1_000,
        volume_5m=4_000,
        volume_15m=9_000,
        volume_5m_buckets=[8_000, 18_000, 35_000],
        buys=200,
        sells=100,
        unique_buyers_series=[30, 55, 90, 140],
        price_history=[
            PricePoint(now - timedelta(minutes=15), 1.0),
            PricePoint(now - timedelta(minutes=10), 2.0),
            PricePoint(now - timedelta(minutes=5), 1.4),
            PricePoint(now - timedelta(minutes=1), 1.5),
        ],
        top10_holder_pct=25.0,
        top20_holder_pct=40.0,
        deployer=DeployerInfo(address="0xdeployer"),
    )
    defaults.update(overrides)
    return TokenSnapshot(**defaults)


def test_strong_setup_scores_as_entry():
    result = score_token(_base_token())
    assert result.verdict == Verdict.ENTRY
    assert result.total >= DEFAULT_CONFIG.entry_score_min


def test_score_caps_at_100_points():
    result = score_token(_base_token())
    assert result.max_total == 100
    assert result.total <= 100


def test_dirty_deployer_is_a_hard_filter_regardless_of_other_factors():
    token = _base_token(deployer=DeployerInfo(address="0xrug", owns_large_pct=True, has_sold=True))
    result = score_token(token)
    assert result.verdict == Verdict.IGNORE
    assert result.total == 0
    assert result.hard_filter_failed is not None
    assert "deployer" in result.hard_filter_failed


def test_wrong_chain_is_rejected():
    token = _base_token(chain="ethereum")
    result = score_token(token)
    assert result.hard_filter_failed == "wrong chain: ethereum"


def test_stagnant_buyer_count_scores_low_and_flags_wash_trading():
    token = _base_token(unique_buyers_series=[300, 310, 315])
    result = score_token(token)
    buyer_factor = next(f for f in result.factors if f.name == "Unique buyer acceleration")
    assert buyer_factor.points < buyer_factor.max_points / 2
    assert "wash" in buyer_factor.note


def test_flat_volume_buckets_score_zero_on_bucket_half_of_volume_factor():
    token = _base_token(volume_5m_buckets=[9_000, 9_400, 9_100])
    result = score_token(token)
    vol_factor = next(f for f in result.factors if f.name == "Volume acceleration")
    assert vol_factor.points < vol_factor.max_points


def test_high_mcap_to_liquidity_ratio_loses_those_points():
    token = _base_token(market_cap_usd=400_000, liquidity=LiquidityInfo(usd=30_000))
    result = score_token(token)
    ratio_factor = next(f for f in result.factors if f.name == "MC/Liquidity")
    assert ratio_factor.points == 0


def test_age_outside_window_scores_zero_on_age_factor():
    now = datetime.now(timezone.utc)
    token = _base_token(created_at=now - timedelta(minutes=90))
    result = score_token(token)
    age_factor = next(f for f in result.factors if f.name == "Age")
    assert age_factor.points == 0


def test_mock_provider_produces_a_spread_of_verdicts():
    tokens = MockProvider().fetch_new_pairs(DEFAULT_CONFIG)
    verdicts = {t.symbol: score_token(t).verdict for t in tokens}
    assert verdicts["SNIPE"] == Verdict.ENTRY
    assert verdicts["RUGWARN"] == Verdict.IGNORE
