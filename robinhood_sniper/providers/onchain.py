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

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from requests.exceptions import HTTPError
from web3 import Web3

from ..config import SniperConfig
from ..models import DeployerInfo, LiquidityInfo, PricePoint, TokenSnapshot
from .base import PairDataProvider
from .dexscreener import DexScreenerProvider
from .history import TokenHistoryStore

MAX_SWAP_LOGS_PER_POOL = 500  # cap RPC calls (one eth_getTransaction per swap) per scan
SWAP_TX_LOOKUP_DELAY_SECONDS = 0.1  # throttle to avoid bursting the public RPC's rate limit
HISTORY_AUGMENT_BUDGET_SECONDS = 30  # cap total time spent on swap-history RPC calls per scan
SCAN_TIME_BUDGET_SECONDS = 75  # hard cap on the whole scan; watching v3+v4 means unbounded candidate counts

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

    def _estimate_block_time_seconds(self, sample_blocks: int = 2000) -> float:
        latest = _with_retry(lambda: self.w3.eth.block_number)
        older = max(latest - sample_blocks, 0)
        if older == latest:
            return 0.25
        t_latest = _with_retry(lambda: self.w3.eth.get_block(latest))["timestamp"]
        t_older = _with_retry(lambda: self.w3.eth.get_block(older))["timestamp"]
        return max((t_latest - t_older) / (latest - older), 0.01)

    def _lookup_symbol_name(self, token_address: str) -> tuple[str, str]:
        try:
            token = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
            return token.functions.symbol().call(), token.functions.name().call()
        except Exception:
            return "?", "?"

    def _cached_symbol_name(self, history_key: str, token_address: str) -> tuple[str, str]:
        entry = self.history.get(history_key)
        if entry["symbol"] is None:
            entry["symbol"], entry["name"] = self._lookup_symbol_name(token_address)
        return entry["symbol"], entry["name"]

    def _extract_buy_wallets(self, logs: list[Any], new_token_is_token0: bool) -> set[str]:
        wallets: set[str] = set()
        for log in logs[:MAX_SWAP_LOGS_PER_POOL]:
            if not is_buy_swap(log["args"]["amount0"], log["args"]["amount1"], new_token_is_token0):
                continue
            try:
                # The Swap event's own `sender` is usually just the router;
                # the transaction's actual sender is the real trading wallet.
                tx = _with_retry(lambda log=log: self.w3.eth.get_transaction(log["transactionHash"]))
                wallets.add(tx["from"])
            except Exception:
                continue
            time.sleep(SWAP_TX_LOOKUP_DELAY_SECONDS)
        return wallets

    def _fetch_new_buy_wallets_v3(
        self, pool_address: str, new_token_is_token0: bool, from_block: int, to_block: int
    ) -> set[str]:
        if from_block > to_block:
            return set()
        pool = self.w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=_POOL_ABI)
        try:
            logs = _with_retry(lambda: pool.events.Swap().get_logs(from_block=from_block, to_block=to_block))
        except Exception:
            return set()  # a pool this fresh may have no state at from_block yet, or the RPC balked
        return self._extract_buy_wallets(logs, new_token_is_token0)

    def _fetch_new_buy_wallets_v4(
        self, pool_id: bytes, new_token_is_currency0: bool, from_block: int, to_block: int
    ) -> set[str]:
        if from_block > to_block:
            return set()
        try:
            logs = _with_retry(
                lambda: self.pool_manager_v4.events.Swap().get_logs(
                    from_block=from_block, to_block=to_block, argument_filters={"id": pool_id}
                )
            )
        except Exception:
            return set()
        return self._extract_buy_wallets(logs, new_token_is_currency0)

    def _augment_with_history(
        self,
        snapshot: TokenSnapshot,
        history_key: str,
        fetch_wallets: Callable[[int, int], set[str]],
        pool_created_block: int,
        latest_block: int,
        do_rpc_update: bool,
    ) -> None:
        """Reads accumulated history into the snapshot, and -- budget permitting
        -- does the RPC work to extend that history with this scan's new data.
        Skipping the RPC update under a tight time budget just means this
        particular scan doesn't add a fresh data point; the next one will."""
        entry = self.history.get(history_key)
        if entry["pool_created_block"] is None:
            entry["pool_created_block"] = pool_created_block

        live_price = snapshot.price_history[-1].price_usd if snapshot.price_history else None

        if do_rpc_update:
            from_block = (entry["last_scanned_block"] + 1) if entry["last_scanned_block"] else pool_created_block
            new_wallets = fetch_wallets(from_block, latest_block)
            self.history.record_buyers(history_key, new_wallets, latest_block)
            if live_price is not None:
                self.history.record_price(history_key, live_price)

        snapshot.unique_buyers_series = self.history.buyer_series(history_key)
        snapshot.price_history = [PricePoint(ts, price) for ts, price in self.history.price_series(history_key)]

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

    def _process_v3_log(
        self, config: SniperConfig, log: Any, block_time_of: Callable[[int], datetime], latest: int, history_deadline: float
    ) -> TokenSnapshot | None:
        bn = log["blockNumber"]
        token0, token1, pool = log["args"]["token0"], log["args"]["token1"], log["args"]["pool"]
        new_token = pick_new_token(token0, token1)
        if new_token is None:
            return None  # both sides already-known base tokens, not a new listing

        snapshot = self._base_snapshot(config, pool, new_token, pool, block_time_of(bn))
        if self.dexscreener is not None:
            snapshot = self.dexscreener.enrich_by_token(snapshot)

        new_token_is_token0 = new_token.lower() == token0.lower()
        self._augment_with_history(
            snapshot,
            pool,
            lambda fb, tb, p=pool, nt0=new_token_is_token0: self._fetch_new_buy_wallets_v3(p, nt0, fb, tb),
            bn,
            latest,
            do_rpc_update=time.monotonic() < history_deadline,
        )
        return snapshot

    def _process_v4_log(
        self, config: SniperConfig, log: Any, block_time_of: Callable[[int], datetime], latest: int, history_deadline: float
    ) -> TokenSnapshot | None:
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
        self._augment_with_history(
            snapshot,
            pool_id_hex,
            lambda fb, tb, pid=pool_id, nt0=new_token_is_currency0: self._fetch_new_buy_wallets_v4(pid, nt0, fb, tb),
            bn,
            latest,
            do_rpc_update=time.monotonic() < history_deadline,
        )
        return snapshot

    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        block_time = self._estimate_block_time_seconds()
        latest = _with_retry(lambda: self.w3.eth.block_number)
        blocks_back = int((config.age_max_minutes * 60) / block_time) + 10
        from_block = max(latest - blocks_back, 0)

        v3_logs = _with_retry(
            lambda: self.factory.events.PoolCreated().get_logs(from_block=from_block, to_block=latest)
        )
        v4_logs = _with_retry(
            lambda: self.pool_manager_v4.events.Initialize().get_logs(from_block=from_block, to_block=latest)
        )

        # Newest first: if a launch wave means the scan can't cover everything
        # within its time budget, the freshest candidates (the ones actually
        # worth sniping) get processed before older ones that are about to
        # age out of the window anyway.
        tagged_logs = sorted(
            [("v3", log) for log in v3_logs] + [("v4", log) for log in v4_logs],
            key=lambda item: item[1]["blockNumber"],
            reverse=True,
        )

        block_timestamps: dict[int, int] = {}

        def block_time_of(block_number: int) -> datetime:
            if block_number not in block_timestamps:
                block_timestamps[block_number] = _with_retry(lambda: self.w3.eth.get_block(block_number))["timestamp"]
            return datetime.fromtimestamp(block_timestamps[block_number], tz=timezone.utc)

        snapshots: list[TokenSnapshot] = []
        history_deadline = time.monotonic() + HISTORY_AUGMENT_BUDGET_SECONDS
        scan_deadline = time.monotonic() + SCAN_TIME_BUDGET_SECONDS

        for kind, log in tagged_logs:
            if time.monotonic() >= scan_deadline:
                break  # remaining (older) candidates are picked up on the next scan

            processor = self._process_v3_log if kind == "v3" else self._process_v4_log
            snapshot = processor(config, log, block_time_of, latest, history_deadline)
            if snapshot is not None and snapshot.age_minutes <= config.age_max_minutes:
                snapshots.append(snapshot)

        self.history.prune(config.age_max_minutes)
        self.history.save()
        return snapshots
