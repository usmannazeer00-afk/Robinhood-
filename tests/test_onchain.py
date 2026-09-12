from robinhood_sniper.providers.onchain import KNOWN_BASE_TOKENS, is_buy_swap, pick_new_token

WETH = next(addr for addr, sym in KNOWN_BASE_TOKENS.items() if sym == "WETH")
USDG = next(addr for addr, sym in KNOWN_BASE_TOKENS.items() if sym == "USDG")
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
