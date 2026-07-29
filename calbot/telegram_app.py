"""Telegram adapter and application factory for calendar-only Calbot."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from calbot.calendar.client import CalendarClient
from calbot.calendar.contracts import TOOLS as CALENDAR_TOOLS
from calbot.calendar.digest import create_calendar_digest
from calbot.concurrency import BlockingBridge
from calbot.config import BotConfig
from calbot.messages import visible_reply_text
from calbot.runtime import BotRuntime


log = logging.getLogger("assistant-bot")
_RUNTIME_KEY = "calbot_runtime"
_CONFIG_KEY = "calbot_config"
_BRIDGE_KEY = "calbot_blocking_bridge"
TELEGRAM_CHUNK_CHARS = 2000
ANONYMOUS_ADMIN_USER_ID = 1087968824


def telegram_chunks(text: str) -> list[str]:
    """Split text below Telegram's UTF-16 limit without losing content."""
    if not text:
        return []
    return [
        text[index : index + TELEGRAM_CHUNK_CHARS]
        for index in range(0, len(text), TELEGRAM_CHUNK_CHARS)
    ]


async def _reply_in_chunks(message, text: str) -> None:
    safe_text = visible_reply_text(text)
    if safe_text is None:
        return
    for chunk in telegram_chunks(safe_text):
        await message.reply_text(chunk)


async def _send_in_chunks(bot, chat_id: int, text: str) -> None:
    safe_text = visible_reply_text(text)
    if safe_text is None:
        return
    for chunk in telegram_chunks(safe_text):
        await bot.send_message(chat_id=chat_id, text=chunk)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep expected rolling-deploy conflicts distinct from real failures."""
    del update
    if isinstance(context.error, Conflict):
        log.warning("Telegram polling handoff conflict; retrying")
        return
    log.error("Unhandled Telegram error", exc_info=context.error)


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    # httpx logs full Telegram API URLs, which contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_runtime(config: BotConfig) -> BotRuntime:
    """Build the two external clients Calbot actually needs."""
    claude = anthropic.Anthropic(
        api_key=config.anthropic_api_key,
        timeout=30.0,
        max_retries=2,
    )
    calendar = CalendarClient(
        service_account_json=config.google_service_account_json,
        calendar_id=config.calendar_id,
        timezone_name=config.timezone,
    )
    return BotRuntime(
        config=config,
        claude_client=claude,
        calendar_client=calendar,
        tools=CALENDAR_TOOLS,
    )


def _components(context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    return data[_RUNTIME_KEY], data[_CONFIG_KEY], data[_BRIDGE_KEY]


def _authorized(update: Update, config: BotConfig) -> bool:
    if not update.effective_chat or update.effective_chat.id != config.allowed_chat_id:
        return False
    if not update.effective_user:
        return False
    message = update.message
    if (
        message is not None and getattr(message, "sender_chat", None) is not None
    ) or update.effective_user.id == ANONYMOUS_ADMIN_USER_ID:
        return False
    return config.actor_allowed(update.effective_user.id)


async def _ask(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    request_id: str = "",
) -> str | None:
    runtime, _, bridge = _components(context)
    user = update.effective_user
    if user is None:
        return None
    return await bridge.run(
        runtime.ask,
        chat_id=update.effective_chat.id,
        user_id=user.id,
        user_text=text,
        sender_display_name=user.first_name or "",
        request_id=request_id,
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, config, _ = _components(context)
    if not _authorized(update, config) or not update.message or not update.message.text:
        return

    message = update.message
    text = message.text.strip()
    if not config.respond_to_all:
        username = context.bot.username or ""
        mention_pattern = (
            re.compile(rf"(?<!\w)@{re.escape(username)}\b", re.IGNORECASE)
            if username
            else None
        )
        mentioned = bool(mention_pattern and mention_pattern.search(text))
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        if not (mentioned or is_reply_to_bot):
            return
        if mention_pattern:
            text = mention_pattern.sub("", text).strip()
        if not text:
            return

    await context.bot.send_chat_action(
        chat_id=message.chat_id,
        action=ChatAction.TYPING,
    )
    try:
        reply = await _ask(
            update,
            context,
            text,
            request_id=f"telegram:{message.chat_id}:{message.message_id}",
        )
        if reply:
            await _reply_in_chunks(message, reply)
    except Exception:
        log.exception("Assistant turn failed")
        await _reply_in_chunks(
            message,
            "i hit an error. please try again in a moment.",
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, config, _ = _components(context)
    if not _authorized(update, config):
        chat_id = update.effective_chat.id if update.effective_chat else "unknown"
        await _reply_in_chunks(
            update.message,
            f"this chat or user isn't authorized. chat id: {chat_id}",
        )
        return
    await _reply_in_chunks(
        update.message,
        (
            f"hey {config.bot_owner}! i manage this group's shared calendar.\n\n"
            "try “dinner at lilia saturday at 8,” “what do we have this weekend?” "
            "or “move friday dinner to 7:30.”\n\n"
            "clear requests are added, updated, or removed right away. if something "
            "important is unclear, i'll ask one quick question first.\n\n"
            "commands: /today  /week  /weekend"
        ).lower(),
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_chat:
        await _reply_in_chunks(
            update.message,
            f"chat id: {update.effective_chat.id}",
        )


def _weekend_window(now: datetime) -> tuple[datetime, datetime]:
    days_to_friday = 4 - now.weekday()
    friday = (now + timedelta(days=days_to_friday)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return friday, friday + timedelta(days=3)


async def _run_digest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    label: str,
    start: datetime,
    end: datetime,
) -> None:
    runtime, config, bridge = _components(context)
    if not _authorized(update, config):
        return
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )
    try:
        reply = await bridge.run(
            create_calendar_digest,
            calendar_client=runtime.cal,
            label=label,
            start=start,
            end=end,
            timezone=config.timezone,
        )
        await _reply_in_chunks(update.message, reply)
    except Exception:
        log.exception("Calendar digest command failed")
        await _reply_in_chunks(
            update.message,
            f"i couldn't load your {label}. please try again.",
        )


async def cmd_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, config, _ = _components(context)
    start, end = _weekend_window(datetime.now(config.tz))
    await _run_digest_command(update, context, "weekend preview", start, end)


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, config, _ = _components(context)
    start = datetime.now(config.tz).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    await _run_digest_command(
        update,
        context,
        "week-ahead summary",
        start,
        start + timedelta(days=7),
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, config, _ = _components(context)
    start = datetime.now(config.tz).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    await _run_digest_command(
        update,
        context,
        "today summary",
        start,
        start + timedelta(days=1),
    )


async def scheduled_digest(context: ContextTypes.DEFAULT_TYPE):
    runtime, config, bridge = _components(context)
    now = datetime.now(config.tz)
    job_name = context.job.data
    if job_name == "weekend" and now.weekday() == 4:
        start, end = _weekend_window(now)
        label = "weekend preview"
    elif job_name == "week_ahead" and now.weekday() == 6:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        end = start + timedelta(days=7)
        label = "week-ahead summary"
    else:
        return

    try:
        reply = await bridge.run(
            create_calendar_digest,
            calendar_client=runtime.cal,
            label=label,
            start=start,
            end=end,
            timezone=config.timezone,
        )
        await _send_in_chunks(context.bot, config.allowed_chat_id, reply)
    except Exception:
        log.exception("Scheduled digest failed")
        await _send_in_chunks(
            context.bot,
            config.allowed_chat_id,
            f"i couldn't load your {label}. please try /week.",
        )


def create_application(
    config: BotConfig | None = None,
    runtime: BotRuntime | None = None,
) -> Application:
    config = config or BotConfig.from_env()
    runtime = runtime or create_runtime(config)
    app = Application.builder().token(config.telegram_token).build()
    app.bot_data[_RUNTIME_KEY] = runtime
    app.bot_data[_CONFIG_KEY] = config
    app.bot_data[_BRIDGE_KEY] = BlockingBridge()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("weekend", cmd_weekend))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    app.job_queue.run_daily(
        scheduled_digest,
        time=time(9, 0, tzinfo=config.tz),
        data="weekend",
    )
    app.job_queue.run_daily(
        scheduled_digest,
        time=time(18, 0, tzinfo=config.tz),
        data="week_ahead",
    )
    return app


def main() -> None:
    configure_logging()
    app = create_application()
    log.info("Bot starting (polling)…")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
