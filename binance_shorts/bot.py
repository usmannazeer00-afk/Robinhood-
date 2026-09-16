"""Telegram bot: send /shortscan for the current 15m short-setup scan, or
/bestshort for just the single best candidate.

Setup:
    1. Create a bot with @BotFather on Telegram, grab the token.
    2. export TELEGRAM_BOT_TOKEN=...
    3. export BINANCE_SOURCE=binance to scan real Binance USDT-M futures
       data (public endpoints, no API key needed). Without it the bot
       serves mock candidates so you can see the output format immediately.
    4. If Binance returns a 451 from your host's IP (it geo/IP-blocks most
       cloud/datacenter ranges at the CDN edge), export BINANCE_PROXY_URL
       to route through an HTTP/HTTPS/SOCKS proxy with an eligible egress IP.
    5. python -m binance_shorts.bot

Commands:
    /shortscan [min_score]  - scan now, return every candidate that clears min_score
    /bestshort               - scan now, return only the single best setup
    /params                  - show the active scanner configuration
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import DEFAULT_CONFIG
from .formatting import format_scan_results, format_symbol_block
from .providers.base import FuturesDataProvider
from .providers.binance import BinanceFuturesProvider
from .providers.mock import MockFuturesProvider
from .scanner import scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("binance_shorts.bot")

TELEGRAM_MESSAGE_LIMIT = 4096


def _build_provider() -> FuturesDataProvider:
    source = os.environ.get("BINANCE_SOURCE", "").lower()
    if source == "binance":
        return BinanceFuturesProvider()
    return MockFuturesProvider()


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


async def shortscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await update.message.reply_text("Scan failed -- data source error, check bot logs.")
        return

    text = format_scan_results(results, markdown=True)
    for chunk in _chunk(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def bestshort_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    provider = _build_provider()
    try:
        results = scan(provider, DEFAULT_CONFIG)
    except Exception:
        logger.exception("scan failed")
        await update.message.reply_text("Scan failed -- data source error, check bot logs.")
        return

    if not results:
        await update.message.reply_text("No candidates right now. Try again shortly.")
        return

    text = format_symbol_block(results[0], markdown=True)
    for chunk in _chunk(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def params_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    c = DEFAULT_CONFIG
    lines = [
        f"Interval: {c.interval} (HTF trend filter: {c.htf_interval})",
        f"Liquidity floor: ${c.min_quote_volume_24h:,.0f} 24h quote volume, top {c.max_symbols_scanned} symbols scanned",
        f"RSI overbought: {c.rsi_overbought:.0f}, EMA{c.ema_fast}/EMA{c.ema_slow}",
        f"Funding 'hot' threshold: {c.funding_rate_hot * 100:.3f}% per funding interval",
        f"Short score: >= {c.entry_score_min:.0f}, Watch: {c.watch_score_min:.0f}-{c.entry_score_min - 1:.0f}",
    ]
    await update.message.reply_text("\n".join(lines))


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("shortscan", shortscan_command))
    app.add_handler(CommandHandler("bestshort", bestshort_command))
    app.add_handler(CommandHandler("params", params_command))
    return app


def main() -> None:
    app = build_app()
    logger.info("Binance 15m short-setup bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
