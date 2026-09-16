from binance_shorts.providers._http import rank_symbols


def _tickers():
    return [
        {"symbol": "AUSDT", "quoteVolume": "5000", "priceChangePercent": "1.5"},
        {"symbol": "BUSDT", "quoteVolume": "1000", "priceChangePercent": "12.0"},
        {"symbol": "CUSDT", "quoteVolume": "9000", "priceChangePercent": "-3.0"},
        {"symbol": "DUSDT", "quoteVolume": "2000", "priceChangePercent": "8.0"},
    ]


def test_rank_symbols_by_quote_volume_default():
    symbols, volumes = rank_symbols(_tickers(), tradeable={"AUSDT", "BUSDT", "CUSDT", "DUSDT"}, rank_by="quote_volume", limit=2)
    assert symbols == ["CUSDT", "AUSDT"]
    assert volumes == {"AUSDT": 5000.0, "BUSDT": 1000.0, "CUSDT": 9000.0, "DUSDT": 2000.0}


def test_rank_symbols_by_gainers():
    symbols, volumes = rank_symbols(_tickers(), tradeable={"AUSDT", "BUSDT", "CUSDT", "DUSDT"}, rank_by="gainers", limit=2)
    assert symbols == ["BUSDT", "DUSDT"]  # highest priceChangePercent first
    assert volumes["BUSDT"] == 1000.0  # quote volumes still returned for the liquidity-floor filter


def test_rank_symbols_excludes_non_tradeable():
    symbols, volumes = rank_symbols(_tickers(), tradeable={"AUSDT"}, rank_by="gainers", limit=10)
    assert symbols == ["AUSDT"]
    assert volumes == {"AUSDT": 5000.0}
