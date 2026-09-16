from unittest.mock import MagicMock

from binance_shorts.config import DEFAULT_CONFIG
from binance_shorts.providers.binance_spot import BinanceSpotProvider


def _kline_row(open_time_ms, o, h, l, c, volume, close_time_ms, taker_buy_base):
    return [open_time_ms, str(o), str(h), str(l), str(c), str(volume), close_time_ms, "0", 10, str(taker_buy_base), "0", "0"]


def _mock_session(exchange_info, tickers, klines_by_interval):
    session = MagicMock()

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if url.endswith("/api/v3/exchangeInfo"):
            resp.json.return_value = exchange_info
        elif url.endswith("/api/v3/ticker/24hr"):
            resp.json.return_value = tickers
        elif url.endswith("/api/v3/klines"):
            resp.json.return_value = klines_by_interval[params["interval"]]
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return resp

    session.get.side_effect = fake_get
    return session


def test_default_api_base_is_the_unrestricted_public_data_mirror():
    provider = BinanceSpotProvider()
    assert provider.api_base == "https://data-api.binance.vision"


def test_liquid_symbols_excludes_non_spot_wrong_quote_and_leveraged_tokens():
    exchange_info = {
        "symbols": [
            {"symbol": "AUSDT", "baseAsset": "A", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "BUSDT", "baseAsset": "B", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "CUSDT", "baseAsset": "C", "quoteAsset": "USDT", "status": "BREAK", "isSpotTradingAllowed": True},
            {"symbol": "DBUSD", "baseAsset": "D", "quoteAsset": "BUSD", "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "BTCUPUSDT", "baseAsset": "BTCUP", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "ETHDOWNUSDT", "baseAsset": "ETHDOWN", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "EUSDT", "baseAsset": "E", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": False},
        ]
    }
    tickers = [
        {"symbol": "AUSDT", "quoteVolume": "1000"},
        {"symbol": "BUSDT", "quoteVolume": "5000"},
        {"symbol": "CUSDT", "quoteVolume": "9999"},
        {"symbol": "DBUSD", "quoteVolume": "9999"},
        {"symbol": "BTCUPUSDT", "quoteVolume": "9999"},
        {"symbol": "ETHDOWNUSDT", "quoteVolume": "9999"},
        {"symbol": "EUSDT", "quoteVolume": "9999"},
    ]
    session = _mock_session(exchange_info, tickers, klines_by_interval={})
    provider = BinanceSpotProvider(session=session)

    symbols, volumes = provider._liquid_symbols(DEFAULT_CONFIG)

    assert symbols == ["BUSDT", "AUSDT"]
    assert volumes == {"AUSDT": 1000.0, "BUSDT": 5000.0}


def test_fetch_symbol_builds_a_snapshot_with_no_funding_or_open_interest():
    ltf_rows = [_kline_row(i * 900_000, 100, 101, 99, 100.5, 1000, i * 900_000 + 899_999, 500) for i in range(5)]
    htf_rows = [_kline_row(i * 3_600_000, 100, 101, 99, 100.5, 1000, i * 3_600_000 + 3_599_999, 500) for i in range(5)]
    session = _mock_session(
        exchange_info={"symbols": []}, tickers=[], klines_by_interval={"15m": ltf_rows, "1h": htf_rows}
    )
    provider = BinanceSpotProvider(session=session)

    snapshot = provider._fetch_symbol("BTCUSDT", quote_volume_24h=999_000_000, config=DEFAULT_CONFIG)

    assert snapshot is not None
    assert snapshot.symbol == "BTCUSDT"
    assert len(snapshot.candles_15m) == 5
    assert len(snapshot.candles_htf) == 5
    assert snapshot.quote_volume_24h == 999_000_000
    assert snapshot.funding_rate is None
    assert snapshot.open_interest is None
    assert snapshot.open_interest_prev is None


def test_fetch_symbol_returns_none_when_klines_fail():
    from requests.exceptions import RequestException

    session = MagicMock()
    session.get.side_effect = RequestException("boom")
    provider = BinanceSpotProvider(session=session)

    assert provider._fetch_symbol("BTCUSDT", quote_volume_24h=1.0, config=DEFAULT_CONFIG) is None
