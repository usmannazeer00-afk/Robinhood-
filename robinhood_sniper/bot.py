"""Telegram bot: send /newpair to get the current scan output.

Setup:
    1. Create a bot with @BotFather on Telegram, grab the token.
    2. export TELEGRAM_BOT_TOKEN=...
    3. (optional) export SNIPER_SOURCE=onchain to watch real PoolCreated
       events on Robinhood Chain (enriched with DexScreener market data),
       or SNIPER_SOURCE=dexscreener for the name-search provider. Without
       it the bot serves mock candidates so you can see the output format
       immediately.
    4. python -m robinhood_sniper.bot

Commands:
    /newpair [min_score]  - scan now and return scored candidates
    /scan                 - alias for /newpair
    /params               - show the active filter configuration
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import DEFAULT_CONFIG
from .formatting import format_scan_results
from .providers.base import PairDataProvider
from .providers.dexscreener import DexScreenerProvider
from .providers.mock import MockProvider
from .providers.onchain import RobinhoodChainFactoryProvider
from .scanner import scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("robinhood_sniper.bot")

TELEGRAM_MESSAGE_LIMIT = 4096


def _build_provider() -> PairDataProvider:
    source = os.environ.get("SNIPER_SOURCE", "").lower()
    if not source and os.environ.get("DEX_LIVE", "").lower() in {"1", "true", "yes"}:
        source = "dexscreener"  # deprecated alias
    if source == "onchain":
        return RobinhoodChainFactoryProvider(dexscreener=DexScreenerProvider())
    if source == "dexscreener":
        return DexScreenerProvider()
    return MockProvider()


def _chunk(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    length = 0
    for line in text.split("\n"):
        if length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def newpair_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    min_score = 0.0
    if context.args:
        try:
            min_score = float(context.args[0])
        except ValueError:
            pass

    provider = _build_provider()
    try:
        results = scan(provider, DEFAULT_CONFIG, min_score=min_score)
    except Exception:
        logger.exception("scan failed")
        await update.message.reply_text("Scan failed — data source error, check bot logs.")
        return

    text = format_scan_results(results, DEFAULT_CONFIG.chain, markdown=True)
    for chunk in _chunk(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def params_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    c = DEFAULT_CONFIG
    lines = [
        f"Chain: {c.chain}",
        f"Age: {c.age_min_minutes:.0f}-{c.age_max_minutes:.0f}m (ideal {c.age_ideal_min:.0f}-{c.age_ideal_max:.0f}m)",
        f"Market cap: ${c.mcap_min:,.0f}-${c.mcap_max:,.0f} (sweet spot ${c.mcap_sweet_min:,.0f}-${c.mcap_sweet_max:,.0f})",
        f"Liquidity: >=${c.liquidity_min:,.0f}, MC/Liq <= {c.mcap_to_liquidity_max:.0f}x",
        f"Buy/sell: >= {c.buy_sell_ratio_min:.1f}x (preferred {c.buy_sell_ratio_preferred_min:.1f}-{c.buy_sell_ratio_preferred_max:.1f}x)",
        f"Top10 holders: <= {c.top10_holder_pct_max:.0f}%, Top20: <= {c.top20_holder_pct_max:.0f}%",
        f"Entry score: >= {c.entry_score_min}, Watch: {c.watch_score_min}-{c.entry_score_min - 1}",
    ]
    await update.message.reply_text("\n".join(lines))


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("newpair", newpair_command))
    app.add_handler(CommandHandler("scan", newpair_command))
    app.add_handler(CommandHandler("params", params_command))
    return app


def main() -> None:
    app = build_app()
    logger.info("Robinhood Chain sniper bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
