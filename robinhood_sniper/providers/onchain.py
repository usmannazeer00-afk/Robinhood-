"""Real on-chain discovery: watches Uniswap v3 and v4 for new pools on Robinhood Chain.

Unlike DexScreenerProvider (a text search — only finds pairs whose token
name/symbol happens to match the query), this watches the pool-creation
events directly, so it catches every new pool regardless of what the
token is named.

v3 pools are discovered via `UniswapV3Factory.PoolCreated` (one contract
per pool). v4 uses a singleton `PoolManager` holding every pool's state,
so new pools show up as `Initialize` events on that one contract instead,
identified by a `PoolId` (bytes32) rather than a per-pool address.
Robinhood Chain runs both — a v4-only token (no v3 pool at all) is
invisible to a v3-only watcher, which is exactly how a real v4 launch
("FoMo", already +228% by the time it was found by direct lookup) went
undetected before this was added.

Verified 2026-09-12 directly against Robinhood Chain mainnet:
  * chain id 4663 (via eth_chainId on the public RPC)
  * https://rpc.mainnet.chain.robinhood.com is live and responsive
  * UniswapV3Factory bytecode at the address below matches the standard
    Uniswap v3 factory selector set (owner/createPool/getPool/setOwner/
    enableFeeAmount/feeAmountTickSpacing/parameters)
  * the v4 PoolManager address's Initialize/Swap event ABIs below were
    checked against real logs -- decoded 813 real Initialize events in
    one window, and a live-matched swap volume of 9k+ Swap events on one
    pool, resolving a real trading wallet from the transaction sender
  * WETH/USDG addresses below are live contracts on-chain
Sources: https://docs.robinhood.com/chain/connecting,
https://docs.robinhood.com/chain/contracts,
https://developers.uniswap.org (v3- and v4-robinhood-chain-deployments,
and the unified /deployments page for the v4 addresses),
https://blog.uniswap.org/robinhood-chain-is-live

This only discovers *that* a pool was created and *which token* is new;
it has no price/volume/holder data. Pair it with an enrichment step
(e.g. DexScreenerProvider.enrich_by_token) to fill in the rest of the
TokenSnapshot before scoring.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from requests.exceptions import HTTPError
from web3 import Web3

logger = logging.getLogger(__name__)

from ..config import SniperConfig
from ..models import DeployerInfo, LiquidityInfo, PricePoint, TokenSnapshot
from .base import PairDataProvider
from .dexscreener import DexScreenerProvider
from .history import TokenHistoryStore

MAX_SWAP_LOGS_PER_POOL = 500  # cap RPC calls (one eth_getTransaction per swap) per scan
SWAP_TX_LOOKUP_DELAY_SECONDS = 0.1  # throttle to avoid bursting the public RPC's rate limit
HISTORY_AUGMENT_BUDGET_SECONDS = 30  # cap total time spent on swap-history RPC calls per scan

# Discovery + DexScreener enrichment is dominated by per-token network I/O
# to a *different* host than our rate-limited chain RPC, so it's safe (and
# necessary at this chain's launch volume -- 1000+ new pools per 45-minute
# window isn't unusual) to parallelize heavily. The RPC-bound swap-history
# phase that follows stays sequential; that's the resource under contention.
IDENTIFY_ENRICH_WORKERS = 20
IDENTIFY_ENRICH_BUDGET_SECONDS = 90

# A launch bundle is multiple distinct wallets buying in the pool's very
# first block -- before public/organic trading could plausibly react to
# the launch. A handful (the deployer's own first buy, maybe one sniper
# bot) is normal; several distinct wallets in that exact block is the
# signature of coordinated, same-block buying set up in advance.
BUNDLE_MIN_SAME_BLOCK_BUYERS = 3

T = TypeVar("T")


def _with_retry(fn: Callable[[], T], retries: int = 2, base_delay: float = 1.0) -> T:
    """Retries on 429 (public RPC rate limit) with exponential backoff.
    Other exceptions propagate immediately -- only rate limiting is transient here."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except HTTPError as e:
            is_rate_limited = e.response is not None and e.response.status_code == 429
            if not is_rate_limited or attempt == retries:
                raise
            time.sleep(base_delay * (2**attempt))
    raise AssertionError("unreachable")  # loop always returns or raises


MAINNET_CHAIN_ID = 4663
TESTNET_CHAIN_ID = 46630
DEFAULT_MAINNET_RPC = "https://rpc.mainnet.chain.robinhood.com"
DEFAULT_TESTNET_RPC = "https://rpc.testnet.chain.robinhood.com"

UNISWAP_V3_FACTORY = Web3.to_checksum_address("0x1f7d7550b1b028f7571e69a784071f0205fd2efa")
UNISWAP_V4_POOL_MANAGER = Web3.to_checksum_address("0x8366a39CC670B4001A1121B8F6A443A643e40951")

KNOWN_BASE_TOKENS: dict[str, str] = {
    Web3.to_checksum_address("0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"): "WETH",
    Web3.to_checksum_address("0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"): "USDG",
    # v4 represents the native currency as the zero address rather than WETH.
    Web3.to_checksum_address("0x0000000000000000000000000000000000000000"): "ETH",
}

_FACTORY_ABI: list[dict[str, Any]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": True, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "pool", "type": "address"},
        ],
        "name": "PoolCreated",
        "type": "event",
    }
]

_ERC20_ABI: list[dict[str, Any]] = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

_POOL_ABI: list[dict[str, Any]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "int256"},
            {"indexed": False, "name": "amount1", "type": "int256"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
        "name": "Swap",
        "type": "event",
    }
]

_V4_POOL_MANAGER_ABI: list[dict[str, Any]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "id", "type": "bytes32"},
            {"indexed": True, "name": "currency0", "type": "address"},
            {"indexed": True, "name": "currency1", "type": "address"},
            {"indexed": False, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "hooks", "type": "address"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
        "name": "Initialize",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "id", "type": "bytes32"},
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "int128"},
            {"indexed": False, "name": "amount1", "type": "int128"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "tick", "type": "int24"},
            {"indexed": False, "name": "fee", "type": "uint24"},
        ],
        "name": "Swap",
        "type": "event",
    },
]


def is_buy_swap(amount0: int, amount1: int, new_token_is_token0: bool) -> bool:
    """A Uniswap Swap (v3 or v4) is a buy of the new token when the pool's
    new-token balance decreased (negative amount = tokens flowing out to
    the trader). The sign convention is identical in both versions."""
    amount_new_token = amount0 if new_token_is_token0 else amount1
    return amount_new_token < 0


def pick_new_token(token0: str, token1: str) -> str | None:
    """Returns whichever side of a pool isn't a known base token.

    None if both sides are recognized base tokens (not a new listing) or
    neither is recognized (ambiguous — caller should default sensibly).
    """
    t0_known = token0 in KNOWN_BASE_TOKENS
    t1_known = token1 in KNOWN_BASE_TOKENS
    if t0_known and not t1_known:
        return token1
    if t1_known and not t0_known:
        return token0
    if not t0_known and not t1_known:
        return token0
    return None


class RobinhoodChainFactoryProvider(PairDataProvider):
    def __init__(
        self,
        rpc_url: str | None = None,
        factory_address: str | None = None,
        pool_manager_v4_address: str | None = None,
        dexscreener: DexScreenerProvider | None = None,
        w3: Web3 | None = None,
        history: TokenHistoryStore | None = None,
    ) -> None:
        self.rpc_url = rpc_url or os.environ.get("ROBINHOOD_RPC_URL", DEFAULT_MAINNET_RPC)
        self.w3 = w3 or Web3(Web3.HTTPProvider(self.rpc_url))
        self.factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(factory_address or UNISWAP_V3_FACTORY),
            abi=_FACTORY_ABI,
        )
        self.pool_manager_v4 = self.w3.eth.contract(
            address=Web3.to_checksum_address(pool_manager_v4_address or UNISWAP_V4_POOL_MANAGER),
            abi=_V4_POOL_MANAGER_ABI,
        )
        self.dexscreener = dexscreener
        self.history = history or TokenHistoryStore()
        self._history_lock = threading.Lock()

    def _estimate_block_time_and_latest_ts(self, sample_blocks: int = 2000) -> tuple[int, int, float]:
        """Returns (latest_block, latest_block_timestamp, seconds_per_block).

        Concurrent per-log timestamp lookups (`eth_getBlock` for every
        distinct block number among potentially thousands of candidates)
        would itself become an RPC bottleneck, so block timestamps are
        approximated by linear interpolation from this single measurement
        rather than fetched individually -- plenty precise for a filter
        that reasons in whole minutes.
        """
        latest = _with_retry(lambda: self.w3.eth.block_number)
        t_latest = _with_retry(lambda: self.w3.eth.get_block(latest))["timestamp"]
        older = max(latest - sample_blocks, 0)
        if older == latest:
            return latest, t_latest, 0.25
        t_older = _with_retry(lambda: self.w3.eth.get_block(older))["timestamp"]
        block_time = max((t_latest - t_older) / (latest - older), 0.01)
        return latest, t_latest, block_time

    def _lookup_symbol_name(self, token_address: str) -> tuple[str, str]:
        try:
            token = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
            return token.functions.symbol().call(), token.functions.name().call()
        except Exception:
            return "?", "?"

    def _cached_symbol_name(self, history_key: str, token_address: str) -> tuple[str, str]:
        with self._history_lock:
            cached_symbol, cached_name = self.history.get(history_key)["symbol"], self.history.get(history_key)["name"]
        if cached_symbol is not None:
            return cached_symbol, cached_name

        symbol, name = self._lookup_symbol_name(token_address)  # RPC call -- deliberately outside the lock

        with self._history_lock:
            entry = self.history.get(history_key)
            if entry["symbol"] is None:
                entry["symbol"], entry["name"] = symbol, name
            return entry["symbol"], entry["name"]

    def _extract_buy_wallets(
        self, logs: list[Any], new_token_is_token0: bool, creation_block: int
    ) -> tuple[set[str], set[str]]:
        """Returns (all buy wallets, buy wallets whose swap was in the pool's
        creation block). The second set is naturally empty on every call
        after the first, since `from_block` moves past `creation_block`
        once a pool has been scanned once -- no separate "only check once"
        bookkeeping needed here, just at the point where it's persisted."""
        wallets: set[str] = set()
        same_block_wallets: set[str] = set()
        for log in logs[:MAX_SWAP_LOGS_PER_POOL]:
            if not is_buy_swap(log["args"]["amount0"], log["args"]["amount1"], new_token_is_token0):
                continue
            try:
                # The Swap event's own `sender` is usually just the router;
                # the transaction's actual sender is the real trading wallet.
                tx = _with_retry(lambda log=log: self.w3.eth.get_transaction(log["transactionHash"]))
                wallets.add(tx["from"])
                if log["blockNumber"] == creation_block:
                    same_block_wallets.add(tx["from"])
            except Exception:
                continue
            time.sleep(SWAP_TX_LOOKUP_DELAY_SECONDS)
        return wallets, same_block_wallets

    def _fetch_new_buy_wallets_v3(
        self, pool_address: str, new_token_is_token0: bool, creation_block: int, from_block: int, to_block: int
    ) -> tuple[set[str], set[str]]:
        if from_block > to_block:
            return set(), set()
        pool = self.w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=_POOL_ABI)
        try:
            logs = _with_retry(lambda: pool.events.Swap().get_logs(from_block=from_block, to_block=to_block))
        except Exception:
            return set(), set()  # a pool this fresh may have no state at from_block yet, or the RPC balked
        return self._extract_buy_wallets(logs, new_token_is_token0, creation_block)

    def _fetch_new_buy_wallets_v4(
        self, pool_id: bytes, new_token_is_currency0: bool, creation_block: int, from_block: int, to_block: int
    ) -> tuple[set[str], set[str]]:
        if from_block > to_block:
            return set(), set()
        try:
            logs = _with_retry(
                lambda: self.pool_manager_v4.events.Swap().get_logs(
                    from_block=from_block, to_block=to_block, argument_filters={"id": pool_id}
                )
            )
        except Exception:
            return set(), set()
        return self._extract_buy_wallets(logs, new_token_is_currency0, creation_block)

    def _augment_with_history(
        self,
        snapshot: TokenSnapshot,
        history_key: str,
        fetch_wallets: Callable[[int, int], tuple[set[str], set[str]]],
        pool_created_block: int,
        latest_block: int,
        do_rpc_update: bool,
    ) -> None:
        """Reads accumulated history into the snapshot, and -- budget permitting
        -- does the RPC work to extend that history with this scan's new data.
        Skipping the RPC update under a tight time budget just means this
        particular scan doesn't add a fresh data point; the next one will.

        This phase runs sequentially (unlike identify+enrich), so the lock
        below is just cheap bookkeeping around it -- `fetch_wallets` (the
        actual RPC work) is the only slow part and isn't itself concurrent
        here."""
        with self._history_lock:
            entry = self.history.get(history_key)
            if entry["pool_created_block"] is None:
                entry["pool_created_block"] = pool_created_block
            from_block = (entry["last_scanned_block"] + 1) if entry["last_scanned_block"] else pool_created_block
            bundle_already_checked = entry["bundle_checked"]

        live_price = snapshot.price_history[-1].price_usd if snapshot.price_history else None

        if do_rpc_update:
            new_wallets, same_block_wallets = fetch_wallets(from_block, latest_block)
            with self._history_lock:
                self.history.record_buyers(history_key, new_wallets, latest_block)
                if live_price is not None:
                    self.history.record_price(history_key, live_price)
                if not bundle_already_checked:
                    self.history.record_bundle_check(
                        history_key, len(same_block_wallets), BUNDLE_MIN_SAME_BLOCK_BUYERS
                    )

        with self._history_lock:
            entry = self.history.get(history_key)
            snapshot.unique_buyers_series = self.history.buyer_series(history_key)
            snapshot.price_history = [PricePoint(ts, price) for ts, price in self.history.price_series(history_key)]
            snapshot.deployer.linked_to_launch_bundles = entry["bundle_detected"]

    def _base_snapshot(
        self, config: SniperConfig, history_key: str, new_token: str, pair_address: str, created_at: datetime
    ) -> TokenSnapshot:
        symbol, name = self._cached_symbol_name(history_key, new_token)
        return TokenSnapshot(
            chain=config.chain,
            pair_address=pair_address,
            token_address=new_token,
            symbol=symbol,
            name=name,
            created_at=created_at,
            market_cap_usd=0.0,
            liquidity=LiquidityInfo(usd=0.0),
            deployer=DeployerInfo(),
        )

    # -- Phase 1: identify + enrich. Network-I/O-bound (mostly DexScreener,
    # a different host than the chain RPC), safe to run with heavy
    # concurrency. Returns (snapshot, history_key, fetch_wallets, pool_created_block)
    # or None -- the swap-history augmentation happens later, sequentially.

    def _identify_and_enrich_v3(
        self, config: SniperConfig, log: Any, block_time_of: Callable[[int], datetime]
    ) -> tuple[TokenSnapshot, str, Callable[[int, int], tuple[set[str], set[str]]], int] | None:
        bn = log["blockNumber"]
        token0, token1, pool = log["args"]["token0"], log["args"]["token1"], log["args"]["pool"]
        new_token = pick_new_token(token0, token1)
        if new_token is None:
            return None  # both sides already-known base tokens, not a new listing

        snapshot = self._base_snapshot(config, pool, new_token, pool, block_time_of(bn))
        if self.dexscreener is not None:
            snapshot = self.dexscreener.enrich_by_token(snapshot)

        new_token_is_token0 = new_token.lower() == token0.lower()
        fetch_wallets = lambda fb, tb, p=pool, nt0=new_token_is_token0, cb=bn: self._fetch_new_buy_wallets_v3(
            p, nt0, cb, fb, tb
        )
        return snapshot, pool, fetch_wallets, bn

    def _identify_and_enrich_v4(
        self, config: SniperConfig, log: Any, block_time_of: Callable[[int], datetime]
    ) -> tuple[TokenSnapshot, str, Callable[[int, int], tuple[set[str], set[str]]], int] | None:
        bn = log["blockNumber"]
        currency0, currency1, pool_id = log["args"]["currency0"], log["args"]["currency1"], log["args"]["id"]
        new_token = pick_new_token(currency0, currency1)
        if new_token is None:
            return None

        pool_id_hex = Web3.to_hex(pool_id)
        snapshot = self._base_snapshot(config, pool_id_hex, new_token, pool_id_hex, block_time_of(bn))
        if self.dexscreener is not None:
            snapshot = self.dexscreener.enrich_by_token(snapshot)

        new_token_is_currency0 = new_token.lower() == currency0.lower()
        fetch_wallets = lambda fb, tb, pid=pool_id, nt0=new_token_is_currency0, cb=bn: self._fetch_new_buy_wallets_v4(
            pid, nt0, cb, fb, tb
        )
        return snapshot, pool_id_hex, fetch_wallets, bn

    def _fetch_creation_logs_or_empty(self, label: str, fn: Callable[[], list[Any]]) -> list[Any]:
        """Losing one version's discovery entirely (crashing the whole scan)
        is worse than losing one token's history, so these two top-level
        queries get a longer retry budget than per-token RPC calls, and a
        hard fallback to "found nothing this version" instead of raising --
        the other version's pools (and this scan's DexScreener-only results)
        still get returned rather than nothing at all."""
        try:
            return _with_retry(fn, retries=4, base_delay=2.0)
        except HTTPError:
            logger.warning("%s pool discovery failed (RPC rate limited) -- skipping this scan", label)
            return []

    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        latest, latest_ts, block_time = self._estimate_block_time_and_latest_ts()
        blocks_back = int((config.age_max_minutes * 60) / block_time) + 10
        from_block = max(latest - blocks_back, 0)

        v3_logs = self._fetch_creation_logs_or_empty(
            "v3", lambda: self.factory.events.PoolCreated().get_logs(from_block=from_block, to_block=latest)
        )
        v4_logs = self._fetch_creation_logs_or_empty(
            "v4", lambda: self.pool_manager_v4.events.Initialize().get_logs(from_block=from_block, to_block=latest)
        )

        # Newest first: under a time budget, the freshest candidates (the
        # ones actually worth sniping) get priority over older ones about
        # to age out of the window anyway.
        tagged_logs = sorted(
            [("v3", log) for log in v3_logs] + [("v4", log) for log in v4_logs],
            key=lambda item: item[1]["blockNumber"],
            reverse=True,
        )

        def block_time_of(block_number: int) -> datetime:
            approx_ts = latest_ts - (latest - block_number) * block_time
            return datetime.fromtimestamp(approx_ts, tz=timezone.utc)

        # Phase 1: identify + enrich every candidate concurrently. This is
        # the phase that scales with total launch volume (which can be in
        # the thousands per window), so it gets real parallelism.
        def identify_and_enrich(item: tuple[str, Any]):
            kind, log = item
            fn = self._identify_and_enrich_v3 if kind == "v3" else self._identify_and_enrich_v4
            try:
                return fn(config, log, block_time_of)
            except Exception:
                logger.exception("failed to identify/enrich a %s candidate -- skipping it", kind)
                return None

        enriched: list[tuple[TokenSnapshot, str, Callable[[int, int], tuple[set[str], set[str]]], int]] = []
        # Deliberately not a `with` block: ThreadPoolExecutor.__exit__ calls
        # shutdown(wait=True) unconditionally, which would block until every
        # submitted task finishes regardless of the `wait(timeout=...)`
        # below -- silently turning the budget into a no-op. shutdown here
        # is explicit and non-blocking instead: cancel_futures drops
        # anything not yet started, and any already-running task (bounded
        # by DexScreener's own short per-call timeout) is left to finish on
        # its own rather than held up for.
        executor = ThreadPoolExecutor(max_workers=IDENTIFY_ENRICH_WORKERS)
        try:
            futures = [executor.submit(identify_and_enrich, item) for item in tagged_logs]
            done, _not_done = wait(futures, timeout=IDENTIFY_ENRICH_BUDGET_SECONDS)
            for future in done:
                result = future.result()
                if result is not None:
                    enriched.append(result)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        # Preserve newest-first order (thread completion order isn't ordered).
        enriched.sort(key=lambda item: item[3], reverse=True)

        # Phase 2: swap-history augmentation, sequential -- this is the
        # RPC-bound phase against our own rate-limited chain RPC, so it
        # keeps its existing fixed time budget regardless of how many
        # candidates phase 1 produced.
        snapshots: list[TokenSnapshot] = []
        history_deadline = time.monotonic() + HISTORY_AUGMENT_BUDGET_SECONDS
        for snapshot, history_key, fetch_wallets, pool_created_block in enriched:
            self._augment_with_history(
                snapshot,
                history_key,
                fetch_wallets,
                pool_created_block,
                latest,
                do_rpc_update=time.monotonic() < history_deadline,
            )
            if snapshot.age_minutes <= config.age_max_minutes:
                snapshots.append(snapshot)

        self.history.prune(config.age_max_minutes)
        self.history.save()
        return snapshots
