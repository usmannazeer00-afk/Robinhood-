from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from robinhood_sniper.providers.onchain import (
    KNOWN_BASE_TOKENS,
    UNISWAP_V3_FACTORY,
    UNISWAP_V4_POOL_MANAGER,
    RobinhoodChainFactoryProvider,
    is_buy_swap,
    pick_new_token,
)
from robinhood_sniper.providers.history import TokenHistoryStore

WETH = next(addr for addr, sym in KNOWN_BASE_TOKENS.items() if sym == "WETH")
USDG = next(addr for addr, sym in KNOWN_BASE_TOKENS.items() if sym == "USDG")
NATIVE_ETH = next(addr for addr, sym in KNOWN_BASE_TOKENS.items() if sym == "ETH")
RANDOM_A = "0x1111111111111111111111111111111111111111"
RANDOM_B = "0x2222222222222222222222222222222222222222"


def test_new_token_is_the_non_base_side():
    assert pick_new_token(WETH, RANDOM_A) == RANDOM_A
    assert pick_new_token(RANDOM_A, WETH) == RANDOM_A
    assert pick_new_token(USDG, RANDOM_A) == RANDOM_A


def test_two_base_tokens_is_not_a_new_listing():
    assert pick_new_token(WETH, USDG) is None


def test_two_unknown_tokens_defaults_to_first():
    assert pick_new_token(RANDOM_A, RANDOM_B) == RANDOM_A


def test_is_buy_swap_when_new_token_is_token0():
    # pool's token0 balance decreased (negative) => tokens went out to the trader => a buy
    assert is_buy_swap(amount0=-500, amount1=200, new_token_is_token0=True) is True
    assert is_buy_swap(amount0=500, amount1=-200, new_token_is_token0=True) is False


def test_is_buy_swap_when_new_token_is_token1():
    assert is_buy_swap(amount0=200, amount1=-500, new_token_is_token0=False) is True
    assert is_buy_swap(amount0=-200, amount1=500, new_token_is_token0=False) is False


def test_v4_native_eth_pairing_is_recognized_as_a_base_token():
    # v4 represents native ETH as the zero address, not WETH
    assert pick_new_token(NATIVE_ETH, RANDOM_A) == RANDOM_A


def test_v3_and_v4_addresses_are_distinct_and_checksummed():
    assert UNISWAP_V3_FACTORY != UNISWAP_V4_POOL_MANAGER
    from web3 import Web3

    assert Web3.is_checksum_address(UNISWAP_V3_FACTORY)
    assert Web3.is_checksum_address(UNISWAP_V4_POOL_MANAGER)


def test_creation_log_fetch_falls_back_to_empty_on_persistent_rate_limit(tmp_path, monkeypatch):
    """Losing one version's discovery to a sustained 429 shouldn't crash the
    whole scan -- it should degrade to 'found nothing this version' so the
    other version's pools (and this run overall) still come back."""
    monkeypatch.setattr("robinhood_sniper.providers.onchain.time.sleep", lambda seconds: None)
    provider = RobinhoodChainFactoryProvider(
        w3=MagicMock(), history=TokenHistoryStore(path=str(tmp_path / "hist.json"))
    )
    response = MagicMock(status_code=429)
    always_rate_limited = MagicMock(side_effect=HTTPError(response=response))

    result = provider._fetch_creation_logs_or_empty("v4", always_rate_limited)

    assert result == []


def test_creation_log_fetch_propagates_non_rate_limit_errors(tmp_path):
    provider = RobinhoodChainFactoryProvider(
        w3=MagicMock(), history=TokenHistoryStore(path=str(tmp_path / "hist.json"))
    )
    always_broken = MagicMock(side_effect=ValueError("not an HTTP error at all"))

    with pytest.raises(ValueError):
        provider._fetch_creation_logs_or_empty("v3", always_broken)
