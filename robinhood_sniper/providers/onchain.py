"""Real on-chain discovery: watches UniswapV3Factory.PoolCreated on Robinhood Chain.

Unlike DexScreenerProvider (a text search — only finds pairs whose token
name/symbol happens to match the query), this watches the factory
contract directly, so it catches every new pool regardless of what the
token is named.

Verified 2026-09-12 directly against Robinhood Chain mainnet:
  * chain id 4663 (via eth_chainId on the public RPC)
  * https://rpc.mainnet.chain.robinhood.com is live and responsive
  * UniswapV3Factory bytecode at the address below matches the standard
    Uniswap v3 factory selector set (owner/createPool/getPool/setOwner/
    enableFeeAmount/feeAmountTickSpacing/parameters)
  * WETH/USDG addresses below are live contracts on-chain
Sources: https://docs.robinhood.com/chain/connecting,
https://docs.robinhood.com/chain/contracts,
https://developers.uniswap.org (v3-robinhood-chain-deployments),
https://blog.uniswap.org/robinhood-chain-is-live

This only discovers *that* a pool was created and *which token* is new;
it has no price/volume/holder data. Pair it with an enrichment step
(e.g. DexScreenerProvider.enrich_by_token) to fill in the rest of the
TokenSnapshot before scoring.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from web3 import Web3

from ..config import SniperConfig
from ..models import DeployerInfo, LiquidityInfo, TokenSnapshot
from .base import PairDataProvider
from .dexscreener import DexScreenerProvider

MAINNET_CHAIN_ID = 4663
TESTNET_CHAIN_ID = 46630
DEFAULT_MAINNET_RPC = "https://rpc.mainnet.chain.robinhood.com"
DEFAULT_TESTNET_RPC = "https://rpc.testnet.chain.robinhood.com"

UNISWAP_V3_FACTORY = Web3.to_checksum_address("0x1f7d7550b1b028f7571e69a784071f0205fd2efa")

KNOWN_BASE_TOKENS: dict[str, str] = {
    Web3.to_checksum_address("0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"): "WETH",
    Web3.to_checksum_address("0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"): "USDG",
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
        dexscreener: DexScreenerProvider | None = None,
        w3: Web3 | None = None,
    ) -> None:
        self.rpc_url = rpc_url or os.environ.get("ROBINHOOD_RPC_URL", DEFAULT_MAINNET_RPC)
        self.w3 = w3 or Web3(Web3.HTTPProvider(self.rpc_url))
        self.factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(factory_address or UNISWAP_V3_FACTORY),
            abi=_FACTORY_ABI,
        )
        self.dexscreener = dexscreener

    def _estimate_block_time_seconds(self, sample_blocks: int = 2000) -> float:
        latest = self.w3.eth.block_number
        older = max(latest - sample_blocks, 0)
        if older == latest:
            return 0.25
        t_latest = self.w3.eth.get_block(latest)["timestamp"]
        t_older = self.w3.eth.get_block(older)["timestamp"]
        return max((t_latest - t_older) / (latest - older), 0.01)

    def _lookup_symbol_name(self, token_address: str) -> tuple[str, str]:
        try:
            token = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
            return token.functions.symbol().call(), token.functions.name().call()
        except Exception:
            return "?", "?"

    def fetch_new_pairs(self, config: SniperConfig) -> list[TokenSnapshot]:
        block_time = self._estimate_block_time_seconds()
        latest = self.w3.eth.block_number
        blocks_back = int((config.age_max_minutes * 60) / block_time) + 10
        from_block = max(latest - blocks_back, 0)

        logs = self.factory.events.PoolCreated().get_logs(from_block=from_block, to_block=latest)

        block_timestamps: dict[int, int] = {}
        snapshots: list[TokenSnapshot] = []
        for log in logs:
            bn = log["blockNumber"]
            if bn not in block_timestamps:
                block_timestamps[bn] = self.w3.eth.get_block(bn)["timestamp"]
            created_at = datetime.fromtimestamp(block_timestamps[bn], tz=timezone.utc)

            token0, token1, pool = log["args"]["token0"], log["args"]["token1"], log["args"]["pool"]
            new_token = pick_new_token(token0, token1)
            if new_token is None:
                continue  # both sides already-known base tokens, not a new listing

            symbol, name = self._lookup_symbol_name(new_token)
            snapshot = TokenSnapshot(
                chain=config.chain,
                pair_address=pool,
                token_address=new_token,
                symbol=symbol,
                name=name,
                created_at=created_at,
                market_cap_usd=0.0,
                liquidity=LiquidityInfo(usd=0.0),
                deployer=DeployerInfo(),
            )

            if self.dexscreener is not None:
                snapshot = self.dexscreener.enrich_by_token(snapshot)

            if snapshot.age_minutes <= config.age_max_minutes:
                snapshots.append(snapshot)
        return snapshots
