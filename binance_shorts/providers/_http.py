"""HTTP helpers shared by the Binance-backed providers."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Callable, TypeVar

from requests.exceptions import HTTPError

if TYPE_CHECKING:
    from ..config import ShortScannerConfig
    from ..models import FuturesSnapshot

REQUEST_TIMEOUT_SECONDS = 10

# Per-symbol kline/funding/OI enrichment is network-I/O-bound against one
# host, so it's safe to parallelize -- mirrors the identify+enrich phase
# in the Robinhood Chain sniper's onchain provider.
ENRICH_WORKERS = 10

T = TypeVar("T")


def with_retry(fn: Callable[[], T], retries: int = 2, base_delay: float = 1.0) -> T:
    """Retries on 429 (rate limit) and 418 (IP auto-ban warning) with
    exponential backoff. Anything else propagates immediately -- those two
    are the only transient/infra-side statuses here, not application bugs."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (429, 418) or attempt == retries:
                raise
            time.sleep(base_delay * (2**attempt))
    raise AssertionError("unreachable")  # loop always returns or raises


def concurrent_fetch(
    fetch_symbol: Callable[[str, float, "ShortScannerConfig"], "FuturesSnapshot | None"],
    symbols: list[str],
    quote_volumes: dict[str, float],
    config: "ShortScannerConfig",
) -> list["FuturesSnapshot"]:
    """Runs fetch_symbol for every symbol concurrently under a fixed time
    budget, dropping whatever hasn't finished when the budget runs out.

    Deliberately not a `with` block -- ThreadPoolExecutor.__exit__ calls
    shutdown(wait=True) unconditionally, which would block until every
    submitted task finishes regardless of the wait(timeout=...) below,
    silently turning the time budget into a no-op. shutdown here is
    explicit and non-blocking instead: cancel_futures drops anything not
    yet started, and any already-running task (bounded by its own short
    per-call timeout) is left to finish on its own rather than held up for.
    """
    snapshots: list[FuturesSnapshot] = []
    executor = ThreadPoolExecutor(max_workers=ENRICH_WORKERS)
    try:
        futures = [executor.submit(fetch_symbol, symbol, quote_volumes.get(symbol, 0.0), config) for symbol in symbols]
        done, _not_done = wait(futures, timeout=config.scan_time_budget_seconds)
        for future in done:
            result = future.result()
            if result is not None:
                snapshots.append(result)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return snapshots
