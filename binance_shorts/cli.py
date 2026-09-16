"""Command-line entry point -- run a 15m short scan without Telegram.

    python -m binance_shorts.cli scan                            # mock data, for demos/tests
    python -m binance_shorts.cli scan --source binance            # real Binance USDT-M futures data
    python -m binance_shorts.cli scan --source binance --best     # only the single best short setup
    python -m binance_shorts.cli scan --min-score 60
    python -m binance_shorts.cli scan --source binance --proxy http://user:pass@host:port
        # route around Binance's geo/IP restriction (a 451 from most cloud
        # hosts) via an HTTP/HTTPS/SOCKS proxy -- or set BINANCE_PROXY_URL
"""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_CONFIG
from .formatting import format_scan_results, format_symbol_block
from .providers.binance import BinanceFuturesProvider
from .providers.mock import MockFuturesProvider
from .scanner import scan


def _build_provider(source: str, proxy_url: str | None = None):
    if source == "binance":
        return BinanceFuturesProvider(proxy_url=proxy_url)
    return MockFuturesProvider()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="binance-shorts")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="scan 15m Binance USDT-M perpetuals for short setups")
    scan_parser.add_argument(
        "--source", choices=["mock", "binance"], default="mock",
        help="mock (default, demo data) or binance (real public REST market data)",
    )
    scan_parser.add_argument("--min-score", type=float, default=0, help="only show symbols scoring at least this many points")
    scan_parser.add_argument("--best", action="store_true", help="only print the single highest-scoring setup")
    scan_parser.add_argument(
        "--proxy", default=None,
        help="HTTP/HTTPS/SOCKS proxy URL for --source binance (overrides BINANCE_PROXY_URL); "
        "use this if Binance returns 451 from your host's IP",
    )

    args = parser.parse_args(argv)

    if args.command == "scan":
        provider = _build_provider(args.source, proxy_url=args.proxy)
        results = scan(provider, DEFAULT_CONFIG, min_score=args.min_score)
        if args.best:
            if not results:
                print("No candidates matched.")
                return 0
            print(format_symbol_block(results[0], markdown=False))
        else:
            print(format_scan_results(results, markdown=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
