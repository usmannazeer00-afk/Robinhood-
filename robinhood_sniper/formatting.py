"""Renders scan results as Telegram-friendly (Markdown) or plain text."""

from __future__ import annotations

from .scanner import ScannedToken

_ENTRY_STRATEGY_NOTE = (
    "Don't market-buy on score alone. Wait for: initial pump -> "
    "20-40% retracement -> higher low -> volume expansion, then enter. "
    "Keep size small and define invalidation before entering. Not financial advice."
)


def format_token_block(item: ScannedToken, markdown: bool = True) -> str:
    t, r = item.token, item.result
    bold = "*" if markdown else ""
    lines = []

    if not r.passed_hard_filters:
        lines.append(f"{bold}🔴 {t.symbol} ({t.name}){bold} — HARD FILTER FAILED")
        lines.append(f"Reason: {r.hard_filter_failed}")
        lines.append(f"Contract: `{t.token_address}`" if markdown else f"Contract: {t.token_address}")
        lines.append(f"Pair: `{t.pair_address}`" if markdown else f"Pair: {t.pair_address}")
        return "\n".join(lines)

    lines.append(f"{r.verdict.emoji} {bold}{t.symbol}{bold} ({t.name}) — {r.total:.0f}/{r.max_total} — {r.verdict.value}")
    lines.append(f"Age: {t.age_minutes:.0f}m | MC: ${t.market_cap_usd:,.0f} | Liq: ${t.liquidity.usd:,.0f}")
    ratio = t.buy_sell_ratio
    lines.append(f"Buy/Sell: {ratio:.2f}x" if ratio is not None else "Buy/Sell: n/a")
    lines.append("")
    for f in r.factors:
        check = "✅" if f.points >= f.max_points else ("➖" if f.points > 0 else "❌")
        lines.append(f"{check} {f.name}: {f.points:.1f}/{f.max_points} — {f.note}")
    lines.append("")
    lines.append(f"Name: {t.symbol} — {t.name}")
    lines.append(f"Contract: `{t.token_address}`" if markdown else f"Contract: {t.token_address}")
    lines.append(f"Pair: `{t.pair_address}`" if markdown else f"Pair: {t.pair_address}")
    lines.append(t.dexscreener_url)

    if r.verdict.value == "ENTRY":
        lines.append("")
        lines.append(_ENTRY_STRATEGY_NOTE)

    return "\n".join(lines)


def format_scan_results(items: list[ScannedToken], chain: str, markdown: bool = True) -> str:
    if not items:
        return f"No new {chain} pairs matched the filter right now. Try again shortly."

    header = f"🎯 New {chain} pairs — {len(items)} candidate(s)\n" + "=" * 32
    blocks = [format_token_block(item, markdown=markdown) for item in items]
    return "\n\n".join([header] + blocks)
