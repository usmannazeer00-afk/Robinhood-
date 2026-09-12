"""Command-line entry point — run a scan without needing Telegram at all.

    python -m robinhood_sniper.cli scan            # mock data, for demos/tests
    python -m robinhood_sniper.cli scan --live      # real DexScreener-backed provider
"""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_CONFIG
from .formatting import format_scan_results
from .providers.dexscreener import DexScreenerProvider
from .providers.mock import MockProvider
from .scanner import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robinhood-sniper")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="scan for new pairs and print the scored output")
    scan_parser.add_argument("--live", action="store_true", help="use the real DexScreener provider instead of mock data")
    scan_parser.add_argument("--min-score", type=float, default=0, help="only show tokens scoring at least this many points")

    args = parser.parse_args(argv)

    if args.command == "scan":
        provider = DexScreenerProvider() if args.live else MockProvider()
        results = scan(provider, DEFAULT_CONFIG, min_score=args.min_score)
        print(format_scan_results(results, DEFAULT_CONFIG.chain, markdown=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
