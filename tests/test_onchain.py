from robinhood_sniper.providers.onchain import KNOWN_BASE_TOKENS, pick_new_token

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
