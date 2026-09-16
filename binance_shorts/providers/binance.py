"""Real Binance USDT-M perpetual futures data, via the public REST API.

Every endpoint used here (exchangeInfo, ticker/24hr, klines, premiumIndex,
openInterestHist) is public market data -- no API key or request signing
needed, so this provider can only ever *read* market state, never place
or manage an order. That keeps it a scanner/alerting tool, matching this
repo's other bot: it surfaces candidates and lets you decide, it never
touches your account.

Binance blocks requests from restricted locations at the CDN edge (a 451
citing its Terms of Service, before the request ever reaches an app
server) -- this affects most cloud/datacenter IP ranges, not just one
provider. `proxy_url` (or `BINANCE_PROXY_URL`) routes every request
through an HTTP/HTTPS/SOCKS proxy with an eligible egress IP instead;
without one, this provider only works from a host Binance doesn't block.
See `binance_spot.py` for a source that's reachable without a proxy at all.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests
from requests.exceptions import RequestException

from ..config import ShortScannerConfig
from ..models import Candle, FuturesSnapshot
from ._http import REQUEST_TIMEOUT_SECONDS, concurrent_fetch, with_retry
from .base import FuturesDataProvider

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://fapi.binance.com"


def parse_klines(raw: list[list[Any]]) -> list[Candle]:
    candles = []
    for row in raw:
        open_time_ms, open_, high, low, close, volume, close_time_ms = row[0:7]
        taker_buy_base = row[9]
        candles.append(
            Candle(
                open_time=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
                taker_buy_base_volume=float(taker_buy_base),
                close_time=datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc),
            )
        )
    return candles


class BinanceFuturesProvider(FuturesDataProvider):
    def __init__(
        self,
        api_base: str | None = None,
        session: requests.Session | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.session = session or requests.Session()

        proxy = proxy_url or os.environ.get("BINANCE_PROXY_URL")
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        def call():
            resp = self.session.get(f"{self.api_base}{path}", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()

        return with_retry(call)

    def _liquid_symbols(self, config: ShortScannerConfig) -> tuple[list[str], dict[str, float]]:
        exchange_info = self._get("/fapi/v1/exchangeInfo")
        tradeable = {
            s["symbol"]
            for s in exchange_info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == config.quote_asset
            and s.get("status") == "TRADING"
        }

        tickers = self._get("/fapi/v1/ticker/24hr")
        quote_volumes = {t["symbol"]: float(t.get("quoteVolume") or 0.0) for t in tickers if t.get("symbol") in tradeable}
        ranked = sorted(quote_volumes, key=lambda sym: quote_volumes[sym], reverse=True)
        return ranked[: config.max_symbols_scanned], quote_volumes

    def _fetch_symbol(self, symbol: str, quote_volume_24h: float, config: ShortScannerConfig) -> FuturesSnapshot | None:
        try:
            klines_15m = parse_klines(
                self._get("/fapi/v1/klines", {"symbol": symbol, "interval": config.interval, "limit": config.klines_limit})
            )
            klines_htf = parse_klines(
                self._get(
                    "/fapi/v1/klines",
                    {"symbol": symbol, "interval": config.htf_interval, "limit": config.htf_klines_limit},
                )
            )

            funding_rate = None
            try:
                premium = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
                last_funding = premium.get("lastFundingRate")
                funding_rate = float(last_funding) if last_funding is not None else None
            except RequestException:
                pass  # funding is a positioning signal, not a hard requirement

            open_interest = None
            open_interest_prev = None
            try:
                oi_hist = self._get("/futures/data/openInterestHist", {"symbol": symbol, "period": "15m", "limit": 8})
                if oi_hist:
                    open_interest = float(oi_hist[-1]["sumOpenInterest"])
                    open_interest_prev = float(oi_hist[0]["sumOpenInterest"])
            except RequestException:
                pass

            return FuturesSnapshot(
                symbol=symbol,
                candles_15m=klines_15m,
                candles_htf=klines_htf,
                quote_volume_24h=quote_volume_24h,
                funding_rate=funding_rate,
                open_interest=open_interest,
                open_interest_prev=open_interest_prev,
            )
        except RequestException:
            logger.warning("failed to fetch data for %s -- skipping", symbol, exc_info=True)
            return None

    def fetch_candidates(self, config: ShortScannerConfig) -> list[FuturesSnapshot]:
        symbols, quote_volumes = self._liquid_symbols(config)
        return concurrent_fetch(self._fetch_symbol, symbols, quote_volumes, config)
