"""Real Binance SPOT market data, via Binance's dedicated public data
mirror (data-api.binance.vision).

`fapi.binance.com` and `api.binance.com` both geo/IP-block most cloud and
datacenter IP ranges at the CDN edge (see `binance.py`'s docstring) --
but Binance also runs a separate, purpose-built public market-data
mirror with no such restriction. It carries no account or trading
capability at all, only read-only spot OHLCV/ticker data, so there's
nothing for an eligibility gate to protect, and it's reachable from
environments the trading APIs reject outright -- no proxy needed.

The tradeoff: this is SPOT data, not futures, so there's no funding rate
or open interest here. The scoring engine's "Positioning" factor
gracefully degrades to "unavailable" for every symbol from this
provider (the same pattern the sniper bot uses for holder/deployer data
it can't source) -- every other factor (trend, structure, rejection,
momentum, volume/order-flow, risk/reward) runs on real spot candles
exactly as it would on futures.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.exceptions import RequestException

from ..config import ShortScannerConfig
from ..models import FuturesSnapshot
from ._http import REQUEST_TIMEOUT_SECONDS, concurrent_fetch, rank_symbols, with_retry
from .base import FuturesDataProvider
from .binance import parse_klines

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://data-api.binance.vision"

# Binance's leveraged tokens (e.g. BTCUPUSDT) are quoted in USDT and trade
# on the spot order book, but they're leveraged ETF-like products
# tracking a multiple of the underlying's daily move, not the underlying
# itself -- including them would silently score a leveraged derivative as
# if its candles were the real spot pair's.
_LEVERAGED_TOKEN_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class BinanceSpotProvider(FuturesDataProvider):
    def __init__(self, api_base: str | None = None, session: requests.Session | None = None) -> None:
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        def call():
            resp = self.session.get(f"{self.api_base}{path}", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()

        return with_retry(call)

    def _liquid_symbols(self, config: ShortScannerConfig) -> tuple[list[str], dict[str, float]]:
        exchange_info = self._get("/api/v3/exchangeInfo")
        tradeable = {
            s["symbol"]
            for s in exchange_info.get("symbols", [])
            if s.get("quoteAsset") == config.quote_asset
            and s.get("status") == "TRADING"
            and s.get("isSpotTradingAllowed", True)
            and not s.get("baseAsset", "").endswith(_LEVERAGED_TOKEN_SUFFIXES)
        }

        tickers = self._get("/api/v3/ticker/24hr")
        return rank_symbols(tickers, tradeable, config.rank_by, config.max_symbols_scanned)

    def _fetch_symbol(self, symbol: str, quote_volume_24h: float, config: ShortScannerConfig) -> FuturesSnapshot | None:
        try:
            klines_15m = parse_klines(
                self._get("/api/v3/klines", {"symbol": symbol, "interval": config.interval, "limit": config.klines_limit})
            )
            klines_htf = parse_klines(
                self._get("/api/v3/klines", {"symbol": symbol, "interval": config.htf_interval, "limit": config.htf_klines_limit})
            )
            return FuturesSnapshot(
                symbol=symbol,
                candles_15m=klines_15m,
                candles_htf=klines_htf,
                quote_volume_24h=quote_volume_24h,
                funding_rate=None,
                open_interest=None,
                open_interest_prev=None,
            )
        except RequestException:
            logger.warning("failed to fetch data for %s -- skipping", symbol, exc_info=True)
            return None

    def fetch_candidates(self, config: ShortScannerConfig) -> list[FuturesSnapshot]:
        symbols, quote_volumes = self._liquid_symbols(config)
        return concurrent_fetch(self._fetch_symbol, symbols, quote_volumes, config)
