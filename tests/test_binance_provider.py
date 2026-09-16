from unittest.mock import MagicMock

from binance_shorts.config import DEFAULT_CONFIG
from binance_shorts.providers.binance import BinanceFuturesProvider, parse_klines


def _kline_row(open_time_ms, o, h, l, c, volume, close_time_ms, taker_buy_base):
    return [open_time_ms, str(o), str(h), str(l), str(c), str(volume), close_time_ms, "0", 10, str(taker_buy_base), "0", "0"]


def test_parse_klines_maps_binance_fields_onto_candle():
    raw = [_kline_row(1_700_000_000_000, 10, 11, 9, 10.5, 100, 1_700_000_899_999, 60)]
    candles = parse_klines(raw)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 10.0 and c.high == 11.0 and c.low == 9.0 and c.close == 10.5
    assert c.volume == 100.0
    assert c.taker_buy_base_volume == 60.0
    assert c.taker_sell_base_volume == 40.0


def _mock_session(exchange_info, tickers, klines_by_interval, premium=None, oi_hist=None):
    session = MagicMock()

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if url.endswith("/fapi/v1/exchangeInfo"):
            resp.json.return_value = exchange_info
        elif url.endswith("/fapi/v1/ticker/24hr"):
            resp.json.return_value = tickers
        elif url.endswith("/fapi/v1/klines"):
            resp.json.return_value = klines_by_interval[params["interval"]]
        elif url.endswith("/fapi/v1/premiumIndex"):
            resp.json.return_value = premium or {"lastFundingRate": "0.0001"}
        elif url.endswith("/futures/data/openInterestHist"):
            resp.json.return_value = oi_hist or [{"sumOpenInterest": "100"}, {"sumOpenInterest": "110"}]
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return resp

    session.get.side_effect = fake_get
    return session


def test_liquid_symbols_filters_to_tradeable_perpetuals_and_ranks_by_volume():
    exchange_info = {
        "symbols": [
            {"symbol": "AUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
            {"symbol": "BUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
            {"symbol": "CUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "BREAK"},  # not trading
            {"symbol": "DBUSD", "contractType": "PERPETUAL", "quoteAsset": "BUSD", "status": "TRADING"},  # wrong quote
            {"symbol": "EUSDT", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT", "status": "TRADING"},  # not perpetual
        ]
    }
    tickers = [
        {"symbol": "AUSDT", "quoteVolume": "1000"},
        {"symbol": "BUSDT", "quoteVolume": "5000"},
        {"symbol": "CUSDT", "quoteVolume": "9999"},
        {"symbol": "DBUSD", "quoteVolume": "9999"},
        {"symbol": "EUSDT", "quoteVolume": "9999"},
    ]
    session = _mock_session(exchange_info, tickers, klines_by_interval={})
    provider = BinanceFuturesProvider(session=session)

    symbols, volumes = provider._liquid_symbols(DEFAULT_CONFIG)

    assert symbols == ["BUSDT", "AUSDT"]
    assert volumes == {"AUSDT": 1000.0, "BUSDT": 5000.0}


def test_fetch_symbol_builds_a_complete_snapshot():
    ltf_rows = [_kline_row(i * 900_000, 100, 101, 99, 100.5, 1000, i * 900_000 + 899_999, 500) for i in range(5)]
    htf_rows = [_kline_row(i * 3_600_000, 100, 101, 99, 100.5, 1000, i * 3_600_000 + 3_599_999, 500) for i in range(5)]
    session = _mock_session(
        exchange_info={"symbols": []},
        tickers=[],
        klines_by_interval={"15m": ltf_rows, "1h": htf_rows},
        premium={"lastFundingRate": "0.00042"},
        oi_hist=[{"sumOpenInterest": "1000"}, {"sumOpenInterest": "1250"}],
    )
    provider = BinanceFuturesProvider(session=session)

    snapshot = provider._fetch_symbol("BTCUSDT", quote_volume_24h=999_000_000, config=DEFAULT_CONFIG)

    assert snapshot is not None
    assert snapshot.symbol == "BTCUSDT"
    assert len(snapshot.candles_15m) == 5
    assert len(snapshot.candles_htf) == 5
    assert snapshot.quote_volume_24h == 999_000_000
    assert snapshot.funding_rate == 0.00042
    assert snapshot.open_interest == 1250.0
    assert snapshot.open_interest_prev == 1000.0


def test_fetch_symbol_survives_funding_and_oi_endpoints_failing():
    from requests.exceptions import RequestException

    ltf_rows = [_kline_row(i * 900_000, 100, 101, 99, 100.5, 1000, i * 900_000 + 899_999, 500) for i in range(5)]
    session = MagicMock()

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if url.endswith("/fapi/v1/klines"):
            resp.json.return_value = ltf_rows
            return resp
        raise RequestException("boom")

    session.get.side_effect = fake_get
    provider = BinanceFuturesProvider(session=session)

    snapshot = provider._fetch_symbol("BTCUSDT", quote_volume_24h=1.0, config=DEFAULT_CONFIG)

    assert snapshot is not None
    assert snapshot.funding_rate is None
    assert snapshot.open_interest is None
    assert snapshot.open_interest_prev is None


def test_fetch_symbol_returns_none_when_klines_fail():
    from requests.exceptions import RequestException

    session = MagicMock()
    session.get.side_effect = RequestException("boom")
    provider = BinanceFuturesProvider(session=session)

    assert provider._fetch_symbol("BTCUSDT", quote_volume_24h=1.0, config=DEFAULT_CONFIG) is None
