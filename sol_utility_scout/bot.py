"""Telegram bot: scans on a schedule and pushes alerts to subscribed chats.

Setup:
    1. Create a bot with @BotFather, grab the token.
    2. export TELEGRAM_BOT_TOKEN=...
    3. (optional) export SCOUT_INTERVAL_MIN=10   # minutes between automatic cycles
    4. python -m sol_utility_scout.bot
    5. In Telegram send /subscribe to receive alerts in that chat.

Commands:
    /scan               - run a cycle now (update watchlist + discover new launches)
    /watchlist          - tracked tokens ranked by conviction
    /all                - include completed (7 days done) and dropped tokens
    /token <CA|SYMBOL>  - full card + post-launch activity log
    /check <CA>         - rate any Solana token right now (not added)
    /flush <CA|SYMBOL> [reason] - drop a watched token
    /subscribe          - push new additions, post-launch events and verdicts here
    /unsubscribe        - stop alerts in this chat
"""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .cli import check_token
from .config import DEFAULT_CONFIG
from .formatting import format_cycle, format_token, format_watchlist
from .tracker import CycleReport, Tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sol_utility_scout.bot")

TELEGRAM_MESSAGE_LIMIT = 4096
_cycle_lock = asyncio.Lock()


def _chunk(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    chunks, current, length = [], [], 0
    for line in text.split("\n"):
        line = line[: limit - 1]
        if length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _reply(update: Update, text: str) -> None:
    for chunk in _chunk(text):
        await update.message.reply_text(chunk, disable_web_page_preview=True)


def _tracker(context: ContextTypes.DEFAULT_TYPE) -> Tracker:
    return context.application.bot_data["tracker"]


def _subscribers(tracker: Tracker) -> list[int]:
    return tracker.store.data.setdefault("subscribers", [])


async def _run_cycle(context: ContextTypes.DEFAULT_TYPE) -> CycleReport:
    async with _cycle_lock:  # the scheduled job and /scan must not write the store concurrently
        return await asyncio.to_thread(_tracker(context).run_cycle)


async def scheduled_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        report = await _run_cycle(context)
    except Exception:
        logger.exception("scheduled cycle failed")
        return
    if not (report.added or report.events or report.completed or report.extended or report.dropped):
        return  # quiet cycle -- don't spam
    text = format_cycle(report, DEFAULT_CONFIG)
    for chat_id in list(_subscribers(_tracker(context))):
        for chunk in _chunk(text):
            try:
                await context.bot.send_message(chat_id, chunk, disable_web_page_preview=True)
            except Exception:
                logger.exception("alert to %s failed", chat_id)
                break


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Scanning Solana launches… (takes ~30-90s)")
    try:
        report = await _run_cycle(context)
    except Exception:
        logger.exception("scan failed")
        await update.message.reply_text("Scan failed — data source error, check bot logs.")
        return
    await _reply(update, format_cycle(report, DEFAULT_CONFIG))


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, format_watchlist(_tracker(context).store, DEFAULT_CONFIG))


async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, format_watchlist(_tracker(context).store, DEFAULT_CONFIG, include_finished=True))


async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /token <contract address or symbol>")
        return
    record = _tracker(context).store.find(context.args[0])
    if not record:
        await update.message.reply_text(f"{context.args[0]} is not tracked. Try /check <CA>.")
        return
    await _reply(update, format_token(record, DEFAULT_CONFIG))


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /check <contract address>")
        return
    try:
        text = await asyncio.to_thread(check_token, _tracker(context), context.args[0])
    except Exception:
        logger.exception("check failed")
        text = "Check failed — data source error."
    await _reply(update, text)


async def flush_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /flush <contract address or symbol> [reason]")
        return
    reason = " ".join(context.args[1:]) or "not worth holding"
    async with _cycle_lock:
        record = await asyncio.to_thread(_tracker(context).flush, context.args[0], reason)
    await update.message.reply_text(f"Flushed ${record['symbol']}." if record else f"{context.args[0]} is not on the watchlist.")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tracker = _tracker(context)
    subs = _subscribers(tracker)
    if update.effective_chat.id not in subs:
        subs.append(update.effective_chat.id)
        async with _cycle_lock:
            tracker.store.save()
    await update.message.reply_text("Subscribed — you'll get new picks, post-launch activity and 7-day verdicts here.")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tracker = _tracker(context)
    subs = _subscribers(tracker)
    if update.effective_chat.id in subs:
        subs.remove(update.effective_chat.id)
        async with _cycle_lock:
            tracker.store.save()
    await update.message.reply_text("Unsubscribed.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(__doc__.split("Commands:")[1].strip())


def build_app() -> Application:
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    tracker = Tracker()
    chat = os.environ.get("SCOUT_ALERT_CHAT_ID")
    if chat and int(chat) not in _subscribers(tracker):
        _subscribers(tracker).append(int(chat))
    app.bot_data["tracker"] = tracker
    for name, fn in (
        ("scan", scan_command), ("watchlist", watchlist_command), ("all", all_command),
        ("token", token_command), ("check", check_command), ("flush", flush_command), ("subscribe", subscribe_command),
        ("unsubscribe", unsubscribe_command), ("start", help_command), ("help", help_command),
    ):
        app.add_handler(CommandHandler(name, fn))
    interval = float(os.environ.get("SCOUT_INTERVAL_MIN", "10")) * 60
    app.job_queue.run_repeating(scheduled_cycle, interval=interval, first=10)
    return app


def main() -> None:
    logger.info("Solana utility scout bot starting (polling)...")
    build_app().run_polling()


if __name__ == "__main__":
    main()
