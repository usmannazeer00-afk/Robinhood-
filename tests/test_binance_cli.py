from binance_shorts import cli
from binance_shorts.config import DEFAULT_CONFIG


def test_rank_by_and_limit_flags_are_threaded_into_the_scan_config(monkeypatch):
    captured = {}

    def fake_scan(provider, config, min_score=0):
        captured["config"] = config
        return []

    monkeypatch.setattr(cli, "scan", fake_scan)

    cli.main(["scan", "--source", "mock", "--rank-by", "gainers", "--limit", "15"])

    assert captured["config"].rank_by == "gainers"
    assert captured["config"].max_symbols_scanned == 15


def test_default_flags_keep_the_default_config_values(monkeypatch):
    captured = {}

    def fake_scan(provider, config, min_score=0):
        captured["config"] = config
        return []

    monkeypatch.setattr(cli, "scan", fake_scan)

    cli.main(["scan", "--source", "mock"])

    assert captured["config"].rank_by == "quote_volume"
    assert captured["config"].max_symbols_scanned == DEFAULT_CONFIG.max_symbols_scanned
