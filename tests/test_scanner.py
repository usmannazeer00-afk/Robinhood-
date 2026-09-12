from robinhood_sniper.config import DEFAULT_CONFIG
from robinhood_sniper.formatting import format_scan_results
from robinhood_sniper.providers.mock import MockProvider
from robinhood_sniper.scanner import scan


def test_scan_ranks_highest_score_first():
    results = scan(MockProvider(), DEFAULT_CONFIG)
    scores = [r.result.total for r in results]
    assert scores == sorted(scores, reverse=True)


def test_min_score_filters_out_weak_candidates():
    results = scan(MockProvider(), DEFAULT_CONFIG, min_score=80)
    assert all(r.result.total >= 80 for r in results)
    assert len(results) < len(scan(MockProvider(), DEFAULT_CONFIG))


def test_format_scan_results_contains_symbols():
    results = scan(MockProvider(), DEFAULT_CONFIG)
    text = format_scan_results(results, DEFAULT_CONFIG.chain, markdown=False)
    assert "SNIPE" in text
    assert "New robinhood pairs" in text


def test_format_scan_results_includes_contract_address():
    results = scan(MockProvider(), DEFAULT_CONFIG)
    text = format_scan_results(results, DEFAULT_CONFIG.chain, markdown=False)
    strong = next(r for r in results if r.token.symbol == "SNIPE")
    assert f"Contract: {strong.token.token_address}" in text


def test_format_scan_results_handles_empty():
    text = format_scan_results([], "robinhood", markdown=False)
    assert "No new robinhood pairs" in text
