"""Renders scan results as Telegram-friendly (Markdown) or plain text."""

from __future__ import annotations

from .scanner import ScannedSymbol

_RISK_NOTE = (
    "Scanner/alerting output only -- it never places trades. Confirm the "
    "setup on the chart yourself, size small, and set the stop BEFORE "
    "entering. Funding, liquidation, and slippage on leveraged futures can "
    "erase an otherwise-correct call. Not financial advice."
)


def format_symbol_block(item: ScannedSymbol, markdown: bool = True) -> str:
    s, r = item.snapshot, item.result
    bold = "*" if markdown else ""

    if not r.passed_hard_filters:
        return f"{bold}🔴 {s.symbol}{bold} -- SKIPPED\nReason: {r.hard_filter_failed}"

    lines = [f"{r.verdict.emoji} {bold}{s.symbol}{bold} -- {r.total:.0f}/{r.max_total} -- {r.verdict.value}"]
    lines.append(f"Price: {s.last_price:.6g} | 24h quote vol: ${s.quote_volume_24h:,.0f}")
    if s.funding_rate is not None:
        lines.append(f"Funding: {s.funding_rate * 100:.3f}%")
    lines.append("")
    for f in r.factors:
        check = "✅" if f.points >= f.max_points else ("➖" if f.points > 0 else "❌")
        lines.append(f"{check} {f.name}: {f.points:.1f}/{f.max_points} -- {f.note}")

    if r.trade_levels is not None:
        lvl = r.trade_levels
        rr = f"{lvl.risk_reward:.1f}:1" if lvl.risk_reward is not None else "n/a"
        lines.append("")
        lines.append(f"Idea: short ~{lvl.entry:.6g} | stop {lvl.stop:.6g} | target {lvl.target:.6g} | R:R {rr}")

    if r.verdict.value == "SHORT":
        lines.append("")
        lines.append(_RISK_NOTE)

    return "\n".join(lines)


def format_scan_results(items: list[ScannedSymbol], markdown: bool = True) -> str:
    if not items:
        return "No Binance USDT-M perpetuals matched the short-setup filter right now. Try again shortly."

    header = f"🎯 15m short scan -- {len(items)} candidate(s)\n" + "=" * 32
    blocks = [format_symbol_block(item, markdown=markdown) for item in items]
    return "\n\n".join([header] + blocks)
