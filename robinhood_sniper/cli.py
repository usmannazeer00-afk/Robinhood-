"""Command-line entry point — run a scan without needing Telegram at all.

    python -m robinhood_sniper.cli scan                    # mock data, for demos/tests
    python -m robinhood_sniper.cli scan --source dexscreener  # DexScreener name-search (limited, see README)
    python -m robinhood_sniper.cli scan --source onchain       # real PoolCreated watcher + DexScreener enrichment

    python -m robinhood_sniper.cli fomo                         # FOMO buys on mock data (nothing saved)
    python -m robinhood_sniper.cli fomo --source onchain        # real data, paper-buys BUY signals
    python -m robinhood_sniper.cli wallets add 0xabc... --label "good trader"
    python -m robinhood_sniper.cli wallets list
    python -m robinhood_sniper.cli positions
"""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_CONFIG
from .formatting import format_fomo_run, format_positions, format_scan_results
from .providers.dexscreener import DexScreenerProvider
from .providers.mock import MOCK_SMART_WALLETS, MockProvider
from .providers.onchain import RobinhoodChainFactoryProvider
from .scanner import scan
from .smart_wallets import SmartWalletBook
from .trading import BuyPolicy, PaperTrader, run_fomo


def _build_provider(source: str):
    if source == "dexscreener":
        return DexScreenerProvider()
    if source == "onchain":
        return RobinhoodChainFactoryProvider(dexscreener=DexScreenerProvider())
    return MockProvider()


def build_fomo_state(source: str) -> tuple[SmartWalletBook, PaperTrader]:
    """Mock runs use an in-memory book seeded with the demo whales and an
    in-memory paper trader, so demos never touch your real wallet book or
    positions file."""
    if source == "mock":
        book = SmartWalletBook(persist=False)
        for address, label in MOCK_SMART_WALLETS.items():
            book.add(address, label)
        return book, PaperTrader(persist=False)
    return SmartWalletBook(), PaperTrader()


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

    fomo_parser = sub.add_parser("fomo", help="find coins with story + backing + smart money, and paper-buy them")
    fomo_parser.add_argument("--source", choices=["mock", "dexscreener", "onchain"], default="mock")
    fomo_parser.add_argument("--size", type=float, default=BuyPolicy.size_usd, help="USD per buy")
    fomo_parser.add_argument("--max-positions", type=int, default=BuyPolicy.max_open_positions)
    fomo_parser.add_argument("--no-buy", action="store_true", help="only report signals, don't record buys")
    fomo_parser.add_argument("--all", action="store_true", help="also show SKIP tokens")

    wallets_parser = sub.add_parser("wallets", help="manage the smart-wallet book")
    wallets_sub = wallets_parser.add_subparsers(dest="wallets_command", required=True)
    wallets_sub.add_parser("list")
    add_parser = wallets_sub.add_parser("add")
    add_parser.add_argument("address")
    add_parser.add_argument("--label")
    rm_parser = wallets_sub.add_parser("remove")
    rm_parser.add_argument("address")

    sub.add_parser("positions", help="show paper positions")

    args = parser.parse_args(argv)

    if args.command == "scan":
        source = "dexscreener" if args.live else args.source
        provider = _build_provider(source)
        results = scan(provider, DEFAULT_CONFIG, min_score=args.min_score)
        print(format_scan_results(results, DEFAULT_CONFIG.chain, markdown=False))
        return 0

    if args.command == "fomo":
        book, trader = build_fomo_state(args.source)
        run = run_fomo(
            _build_provider(args.source),
            book,
            None if args.no_buy else trader,
            BuyPolicy(size_usd=args.size, max_open_positions=args.max_positions),
        )
        print(format_fomo_run(run, DEFAULT_CONFIG.chain, markdown=False, show_skips=args.all))
        return 0

    if args.command == "wallets":
        book = SmartWalletBook()
        if args.wallets_command == "add":
            book.add(args.address, args.label)
            book.save()
            print(f"added {book.describe(args.address)}")
        elif args.wallets_command == "remove":
            print("removed" if book.remove(args.address) else "not in book")
            book.save()
        else:
            smart = book.smart_wallets()
            print("\n".join(book.describe(a) for a in smart) if smart else "No smart wallets yet.")
        return 0

    if args.command == "positions":
        print(format_positions(PaperTrader().open_positions()))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
