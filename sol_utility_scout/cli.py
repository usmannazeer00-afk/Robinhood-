"""Command-line entry point.

    python -m sol_utility_scout.cli scan               # one cycle: update watchlist + discover new launches
    python -m sol_utility_scout.cli loop --every 10    # run a cycle every 10 minutes, forever
    python -m sol_utility_scout.cli watchlist [--all]  # ranked by conviction
    python -m sol_utility_scout.cli token <CA|SYMBOL>  # full card + 7-day activity log
    python -m sol_utility_scout.cli check <CA>         # rate any Solana token right now (not added)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .config import DEFAULT_CONFIG
from .formatting import format_cycle, format_token, format_watchlist
from .rating import rate
from .store import Store
from .tracker import Tracker


def check_token(tracker: Tracker, address: str) -> str:
    pairs = tracker.dex.pairs_for_tokens([address])[address]
    if not pairs:
        return f"No Solana trading pair found for {address}"
    snap, text = tracker.observe(address, {}, pairs)
    r = rate(snap, tracker.config, text)
    w = tracker.config.weights
    lines = [
        f"${snap.symbol} — {snap.name} [{r.category}] launched {snap.launched_at}",
        f"Rating {r.total:.0f}/100 = utility {r.utility:.0f}/{w.utility} · socials {r.socials:.0f}/{w.socials} · "
        f"safety {r.safety:.0f}/{w.safety} · market {r.market:.0f}/{w.market} · traction {r.traction:.0f}/{w.traction}",
    ]
    if r.rejected:
        lines.append("REJECTED: " + "; ".join(r.reject_reasons))
    lines += [f"  - {n}" for n in r.notes]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sol-utility-scout")
    parser.add_argument("--store", help="watchlist JSON path (default: $SCOUT_STORE_PATH or .sol_utility_scout.json)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="update tracked tokens and discover new launches (one cycle)")
    loop = sub.add_parser("loop", help="run a cycle every N minutes")
    loop.add_argument("--every", type=float, default=10, help="minutes between cycles (default 10)")
    wl = sub.add_parser("watchlist", help="show tracked tokens ranked by conviction")
    wl.add_argument("--all", action="store_true", help="include completed and dropped tokens")
    tk = sub.add_parser("token", help="full card and activity log for one tracked token")
    tk.add_argument("query", help="contract address or symbol")
    ck = sub.add_parser("check", help="rate any Solana token right now without adding it")
    ck.add_argument("address")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = Store(args.store)

    if args.command == "watchlist":
        print(format_watchlist(store, DEFAULT_CONFIG, include_finished=args.all))
        return 0
    if args.command == "token":
        record = store.find(args.query)
        if not record:
            print(f"{args.query} is not tracked")
            return 1
        print(format_token(record, DEFAULT_CONFIG))
        return 0

    tracker = Tracker(store=store)
    if args.command == "check":
        print(check_token(tracker, args.address))
        return 0
    if args.command == "scan":
        print(format_cycle(tracker.run_cycle()))
        return 0
    if args.command == "loop":
        while True:
            try:
                print(format_cycle(tracker.run_cycle()), flush=True)
            except Exception:  # noqa: BLE001 -- keep the loop alive through transient API failures
                logging.exception("cycle failed")
            time.sleep(args.every * 60)
    return 1


if __name__ == "__main__":
    sys.exit(main())
