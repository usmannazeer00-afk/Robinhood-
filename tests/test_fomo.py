from datetime import datetime, timezone

from robinhood_sniper.config import DEFAULT_CONFIG
from robinhood_sniper.fomo import FomoAction, evaluate, match_themes, DEFAULT_THEMES
from robinhood_sniper.models import LiquidityInfo, TokenProfile, TokenSnapshot
from robinhood_sniper.providers.dexscreener import DexScreenerProvider
from robinhood_sniper.providers.history import TokenHistoryStore
from robinhood_sniper.providers.mock import MOCK_SMART_WALLETS, MockProvider
from robinhood_sniper.scanner import scan
from robinhood_sniper.smart_wallets import SmartWalletBook
from robinhood_sniper.trading import BuyPolicy, PaperTrader, run_fomo


def _demo_book() -> SmartWalletBook:
    book = SmartWalletBook(persist=False)
    for addr, label in MOCK_SMART_WALLETS.items():
        book.add(addr, label)
    return book


def _by_symbol(results):
    return {r.token.symbol: r for r in results}


def test_mock_strong_token_is_buy_and_others_are_not():
    results = [evaluate(s, _demo_book()) for s in scan(MockProvider(seed=1), DEFAULT_CONFIG)]
    by = _by_symbol(results)
    assert by["SNIPE"].action is FomoAction.BUY
    assert [b.rank for b in by["SNIPE"].smart_buyers] == [4, 10]
    assert by["RUGWARN"].action is FomoAction.SKIP
    assert any("hard filter" in r for r in by["RUGWARN"].reasons)
    assert by["FAKEVOL"].action is not FomoAction.BUY


def test_no_smart_wallets_means_no_buy():
    results = [evaluate(s, SmartWalletBook(persist=False)) for s in scan(MockProvider(seed=1), DEFAULT_CONFIG)]
    snipe = _by_symbol(results)["SNIPE"]
    assert snipe.action is not FomoAction.BUY
    assert any("smart wallet" in r for r in snipe.reasons)


def test_missing_backing_blocks_buy():
    scanned = next(s for s in scan(MockProvider(seed=1), DEFAULT_CONFIG) if s.token.symbol == "SNIPE")
    scanned.token.profile.websites = []
    scanned.token.profile.socials = {"telegram": "https://t.me/x"}
    result = evaluate(scanned, _demo_book())
    assert result.action is not FomoAction.BUY
    assert "no website or X account" in result.reasons


def test_theme_matching_is_whole_word():
    now = datetime.now(timezone.utc)
    token = TokenSnapshot("robinhood", "0xp", "0xt", "PAIN", "Painting", now, 0, LiquidityInfo())
    assert match_themes(token, DEFAULT_THEMES) == []  # "ai" inside "painting" must not match
    token.profile = TokenProfile(description="an AI agent that trades")
    assert match_themes(token, DEFAULT_THEMES) == ["AI / agents"]


def test_run_fomo_buys_once_and_respects_position_cap():
    book, trader = _demo_book(), PaperTrader(persist=False)
    run1 = run_fomo(MockProvider(seed=1), book, trader)
    assert [p.symbol for p in run1.bought] == ["SNIPE"]
    run2 = run_fomo(MockProvider(seed=1), book, trader)
    assert run2.bought == []
    assert run2.skipped_buys[0][1] == "already holding"

    capped = run_fomo(MockProvider(seed=1), book, PaperTrader(persist=False), BuyPolicy(max_open_positions=0))
    assert capped.bought == [] and "max 0" in capped.skipped_buys[0][1]


def test_paper_positions_persist(tmp_path):
    path = tmp_path / "pos.json"
    run_fomo(MockProvider(seed=1), _demo_book(), PaperTrader(str(path)))
    positions = PaperTrader(str(path)).open_positions()
    assert len(positions) == 1 and positions[0].mode == "paper" and positions[0].size_usd == 25


def _history_with_run(tmp_path, pool, buyers, prices, bundle=False):
    store = TokenHistoryStore(str(tmp_path / "h.json"))
    store.record_buyers(pool, buyers, last_block=1)
    entry = store.get(pool)
    entry["price_points"] = [[datetime.now(timezone.utc).isoformat(), p] for p in prices]
    entry["bundle_detected"] = bundle
    return store


def test_learns_smart_wallets_from_pools_that_ran(tmp_path):
    book = SmartWalletBook(str(tmp_path / "w.json"), min_wins=2)
    early = ["0xaa", "0xbb"]
    book.learn_from_history(_history_with_run(tmp_path, "0xpool1", early, [1.0, 4.0]))
    assert book.smart_wallets() == []  # one win isn't a track record
    newly = book.learn_from_history(_history_with_run(tmp_path, "0xpool2", early, [1.0, 3.5]))
    assert newly == ["0xaa", "0xbb"]
    # same pool again doesn't double count
    book2 = SmartWalletBook(persist=False, min_wins=2)
    store = _history_with_run(tmp_path, "0xpool1", early, [1.0, 4.0])
    book2.learn_from_history(store)
    book2.learn_from_history(store)
    assert book2.smart_wallets() == []


def test_learning_ignores_flat_and_bundled_pools(tmp_path):
    book = SmartWalletBook(persist=False, min_wins=1)
    book.learn_from_history(_history_with_run(tmp_path, "0xflat", ["0xaa"], [1.0, 1.5]))
    book.learn_from_history(_history_with_run(tmp_path, "0xbundle", ["0xbb"], [1.0, 10.0], bundle=True))
    assert book.smart_wallets() == []


def test_history_keeps_early_buyer_order(tmp_path):
    store = TokenHistoryStore(str(tmp_path / "h.json"))
    store.record_buyers("0xP", ["0xC", "0xA"], last_block=1)
    store.record_buyers("0xP", ["0xA", "0xB"], last_block=2)
    assert store.get("0xp")["early_buyers"] == ["0xc", "0xa", "0xb"]


def test_dexscreener_profile_parsing():
    raw = {
        "chainId": "robinhood", "pairAddress": "0xpair", "pairCreatedAt": 1_700_000_000_000,
        "baseToken": {"address": "0xtok", "symbol": "T", "name": "Tok"},
        "info": {
            "imageUrl": "https://img",
            "websites": [{"url": "https://site", "label": "Website"}],
            "socials": [{"type": "twitter", "url": "https://x.com/t"}],
        },
        "boosts": {"active": 2},
    }
    snap = DexScreenerProvider._to_snapshot(raw)
    assert snap.profile.websites == ["https://site"]
    assert snap.profile.socials == {"twitter": "https://x.com/t"}
    assert snap.profile.has_image and snap.profile.boosts_active == 2

    DexScreenerProvider.apply_profile_row(snap, {
        "description": "a story", "cto": True,
        "links": [{"type": "telegram", "url": "https://t.me/t"}, {"label": "Docs", "url": "https://docs"}],
    })
    assert snap.profile.description == "a story" and snap.profile.community_takeover
    assert snap.profile.socials["telegram"] == "https://t.me/t"
    assert "https://docs" in snap.profile.websites
