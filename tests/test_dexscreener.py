from datetime import datetime, timezone
from unittest.mock import MagicMock

from robinhood_sniper.models import DeployerInfo, LiquidityInfo, TokenSnapshot
from robinhood_sniper.providers.dexscreener import DexScreenerProvider


def _snapshot(pair_address="0xnewpool", created_at=None) -> TokenSnapshot:
    return TokenSnapshot(
        chain="robinhood", pair_address=pair_address, token_address="0xtoken",
        symbol="TEST", name="Test", created_at=created_at or datetime.now(timezone.utc),
        market_cap_usd=0.0, liquidity=LiquidityInfo(usd=0.0), deployer=DeployerInfo(),
    )


def _pair(pair_address: str, created_ms: int, liquidity_usd: float = 10_000) -> dict:
    return {
        "chainId": "robinhood",
        "pairAddress": pair_address,
        "baseToken": {"address": "0xtoken", "symbol": "TEST", "name": "Test"},
        "pairCreatedAt": created_ms,
        "liquidity": {"usd": liquidity_usd},
        "volume": {},
        "txns": {"m5": {}},
    }


def test_enrich_by_token_finds_an_older_sibling_pool_across_all_pairs():
    """A token can have many pools; the earliest pairCreatedAt among ALL of
    them (not just the one matching this snapshot's pair) is what reveals an
    old, already-established token re-pooling as if it were a fresh launch."""
    old_ms = 1_700_000_000_000  # months before the "new" pool below
    new_ms = 1_800_000_000_000
    session = MagicMock()
    session.get.return_value.json.return_value = [
        _pair("0xoldpool", old_ms),
        _pair("0xnewpool", new_ms),
    ]
    provider = DexScreenerProvider(session=session)

    result = provider.enrich_by_token(_snapshot(pair_address="0xnewpool"))

    assert result.token_first_pool_created_at == datetime.fromtimestamp(old_ms / 1000, tz=timezone.utc)


def test_enrich_by_token_with_a_single_pool_uses_its_own_creation_time():
    ms = 1_800_000_000_000
    session = MagicMock()
    session.get.return_value.json.return_value = [_pair("0xnewpool", ms)]
    provider = DexScreenerProvider(session=session)

    result = provider.enrich_by_token(_snapshot(pair_address="0xnewpool"))

    assert result.token_first_pool_created_at == datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
