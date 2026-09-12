"""Command-line entry point — run a scan without needing Telegram at all.

    python -m robinhood_sniper.cli scan                    # mock data, for demos/tests
    python -m robinhood_sniper.cli scan --source dexscreener  # DexScreener name-search (limited, see README)
    python -m robinhood_sniper.cli scan --source onchain       # real PoolCreated watcher + DexScreener enrichment
"""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_CONFIG
from .formatting import format_scan_results
from .providers.dexscreener import DexScreenerProvider
from .providers.mock import MockProvider
from .providers.onchain import RobinhoodChainFactoryProvider
from .scanner import scan


def _build_provider(source: str):
    if source == "dexscreener":
        return DexScreenerProvider()
    if source == "onchain":
        return RobinhoodChainFactoryProvider(dexscreener=DexScreenerProvider())
    return MockProvider()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robinhood-sniper")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="scan for new pairs and print the scored output")
    scan_parser.add_argument(
        "--source", choices=["mock", "dexscreener", "onchain"], default="mock",
        help="mock (default, demo data), dexscreener (name-search, limited), onchain (real PoolCreated watcher)",
    )
    scan_parser.add_argument("--live", action="store_true", help="deprecated alias for --source dexscreener")
    scan_parser.add_argument("--min-score", type=float, default=0, help="only show tokens scoring at least this many points")

    args = parser.parse_args(argv)

    if args.command == "scan":
        source = "dexscreener" if args.live else args.source
        provider = _build_provider(source)
        results = scan(provider, DEFAULT_CONFIG, min_score=args.min_score)
        print(format_scan_results(results, DEFAULT_CONFIG.chain, markdown=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
