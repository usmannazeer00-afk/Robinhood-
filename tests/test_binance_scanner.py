from binance_shorts.config import DEFAULT_CONFIG
from binance_shorts.providers.mock import MockFuturesProvider
from binance_shorts.scanner import best_short, scan


def test_scan_ranks_highest_score_first():
    results = scan(MockFuturesProvider(), DEFAULT_CONFIG)
    scores = [r.result.total for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].snapshot.symbol == "SHORTMEUSDT"


def test_scan_respects_min_score_floor():
    results = scan(MockFuturesProvider(), DEFAULT_CONFIG, min_score=50)
    assert all(r.result.total >= 50 for r in results)
    assert any(r.snapshot.symbol == "SHORTMEUSDT" for r in results)
    assert not any(r.snapshot.symbol == "MOONUSDT" for r in results)


def test_best_short_returns_the_single_top_candidate():
    top = best_short(MockFuturesProvider(), DEFAULT_CONFIG)
    assert top is not None
    assert top.snapshot.symbol == "SHORTMEUSDT"
