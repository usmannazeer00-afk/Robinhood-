"""Text output for the CLI and Telegram (plain text -- no Markdown escaping headaches)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import DEFAULT_CONFIG, ScoutConfig
from .conviction import label
from .store import Store
from .tracker import CycleReport

ICONS = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}


def _usd(v: float | None) -> str:
    if not v:
        return "$0"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suffix}"
    return f"${v:,.0f}"


def _age(iso: str | None) -> str:
    if not iso:
        return "?"
    hours = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.1f}d"


def _trend(record: dict[str, Any]) -> str:
    hist = record.get("conviction_history") or []
    if len(hist) < 2:
        return "•"
    diff = hist[-1][1] - hist[-2][1]
    return "▲" if diff > 0.5 else "▼" if diff < -0.5 else "•"


def token_line(record: dict[str, Any], config: ScoutConfig = DEFAULT_CONFIG) -> str:
    snap = record.get("last_snapshot") or {}
    lab = label(record["conviction"], config)
    mult = (snap.get("market_cap") or 0) / record["first_mcap"] if record.get("first_mcap") else 0
    return (
        f"{ICONS[lab]} ${record['symbol']} [{record['category']}] conviction {record['conviction']:.0f} {_trend(record)} "
        f"(rating {record['rating']:.0f}) | MC {_usd(snap.get('market_cap'))} ({mult:.1f}x) | "
        f"Liq {_usd(snap.get('liquidity_usd'))} | holders {snap.get('safety', {}).get('holders') or '?'} | age {_age(record.get('launched_at'))}"
    )


def format_watchlist(store: Store, config: ScoutConfig = DEFAULT_CONFIG, include_finished: bool = False) -> str:
    groups = [("Watching (7-day window)", "watching")]
    if include_finished:
        groups += [("Completed 7-day tracking", "completed"), ("Dropped", "dropped")]
    lines: list[str] = []
    for title, status in groups:
        records = sorted(store.by_status(status), key=lambda r: -r["conviction"])
        lines.append(f"── {title}: {len(records)}")
        for r in records:
            lines.append(token_line(r, config))
            if status != "watching" and r.get("status_reason"):
                lines.append(f"    ↳ {r['status_reason']}")
    if not store.tokens:
        lines.append("Nothing tracked yet -- run a scan.")
    return "\n".join(lines)


def format_token(record: dict[str, Any], config: ScoutConfig = DEFAULT_CONFIG, max_events: int = 15) -> str:
    snap = record.get("last_snapshot") or {}
    socials = snap.get("socials") or {}
    safety = snap.get("safety") or {}
    b = record.get("breakdown") or {}
    w = config.weights
    lines = [
        f"${record['symbol']} — {record['name']}  [{record['category']}]",
        f"CA: {record['address']}",
        f"https://dexscreener.com/solana/{record['address']}",
        f"Status: {record['status']}{' — ' + record['status_reason'] if record.get('status_reason') else ''}",
        f"Conviction: {record['conviction']:.0f} ({label(record['conviction'], config)}) {_trend(record)}  peak {record['conviction_peak']:.0f}, started {record['first_rating']:.0f}",
        f"Rating now: {record['rating']:.0f}/100  = utility {b.get('utility', 0):.0f}/{w.utility} · socials {b.get('socials', 0):.0f}/{w.socials} · "
        f"safety {b.get('safety', 0):.0f}/{w.safety} · market {b.get('market', 0):.0f}/{w.market} · traction {b.get('traction', 0):.0f}/{w.traction}",
        f"Launched {_age(record.get('launched_at'))} ago · MC {_usd(snap.get('market_cap'))} (first {_usd(record.get('first_mcap'))}, peak {_usd(record.get('peak_mcap'))}) · "
        f"Liq {_usd(snap.get('liquidity_usd'))} · Vol24 {_usd(snap.get('volume_h24'))}",
        f"Safety: mint {'revoked' if safety.get('mint_revoked') else 'ACTIVE' if safety.get('mint_revoked') is False else '?'}, "
        f"freeze {'revoked' if safety.get('freeze_revoked') else 'ACTIVE' if safety.get('freeze_revoked') is False else '?'}, "
        f"LP locked {safety.get('lp_locked_pct') or 0:.0f}%, top10 {safety.get('top10_pct') or 0:.0f}%, holders {safety.get('holders') or '?'}",
        "Links: " + (" · ".join(f"{k}: {socials[k]}" for k in ("website", "twitter", "telegram", "discord", "github", "docs") if socials.get(k)) or "none"),
    ]
    if snap.get("telegram_members"):
        lines.append(f"Telegram members: {snap['telegram_members']:,}")
    if record.get("notes"):
        lines.append("Notes: " + "; ".join(record["notes"]))
    if snap.get("description"):
        lines.append("About: " + snap["description"][:300])
    events = record.get("events") or []
    if events:
        lines.append(f"Activity log (last {min(max_events, len(events))} of {len(events)}):")
        for ts, kind, msg, weight in events[-max_events:]:
            tag = f" ({weight:+.0f})" if weight else ""
            lines.append(f"  {ts[5:16].replace('T', ' ')}  {msg}{tag}")
    return "\n".join(lines)


def format_cycle(report: CycleReport, config: ScoutConfig = DEFAULT_CONFIG) -> str:
    lines = [
        f"Scan: {report.discovered} Solana tokens in discovery feeds, {report.evaluated} new launches evaluated, "
        f"{len(report.added)} added, {report.rejected} filtered · {report.updated} tracked tokens updated"
    ]
    if report.added:
        lines.append("\n🆕 New on watchlist:")
        for r in report.added:
            lines.append(token_line(r, config))
            if r.get("notes"):
                lines.append("    " + "; ".join(r["notes"][:3]))
    if report.events:
        lines.append("\n📈 Post-launch activity:")
        for r, ev in report.events:
            lines.append(f"  ${r['symbol']}: {ev.message} ({ev.weight:+.0f}) → conviction {r['conviction']:.0f}")
    for title, items in (("✅ Finished 7-day tracking", report.completed), ("⛔ Dropped", report.dropped)):
        if items:
            lines.append(f"\n{title}:")
            for r in items:
                lines.append(f"  ${r['symbol']}: {r['status_reason']}")
    return "\n".join(lines)
