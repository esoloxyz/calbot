"""
Couple Calendar Bot
-------------------
A Telegram bot for two people + one shared Google Calendar, powered by Claude.

Talk to it in plain English in your private group:
  "dinner at Lilia saturday 8pm"
  "what do we have this weekend?"
  "move friday's thing to 7"

It also posts automatic digests:
  - Friday 9:00 AM  -> weekend preview
  - Sunday 6:00 PM  -> week ahead
"""

import json
import logging
import os
from collections import defaultdict, deque
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from calendar_client import TOOLS, CalendarClient

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
log = logging.getLogger("couple-bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])  # your private group's chat id
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
COUPLE_NAMES = os.environ.get("COUPLE_NAMES", "the couple")  # e.g. "Ezra and Maya"
RESPOND_TO_ALL = os.environ.get("RESPOND_TO_ALL", "true").lower() == "true"

TZ = ZoneInfo(TIMEZONE)

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
cal = CalendarClient()

# Rolling conversation memory per chat (survives until process restart)
HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=24))

MAX_TOOL_ROUNDS = 8


def system_prompt() -> str:
    now = datetime.now(TZ)
    return f"""You are the shared calendar assistant for {COUPLE_NAMES}, living in a private Telegram group.

Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} ({TIMEZONE}).

Your job:
- When they mention plans, reservations, appointments, trips, etc., add them to the shared calendar using tools. Infer sensible details (default dinner length 2h, appointments 1h). Resolve relative dates ("saturday", "next friday") against the current date above.
- Answer questions about their schedule by listing events first, then summarizing warmly and concisely.
- Update or delete events when asked. If a delete request is ambiguous, list matches and ask which one.
- If a message is just chat between the two of them and clearly not for you, reply with exactly: PASS

Style:
- Brief and warm. Confirm actions in one line, e.g. "Added ✓ Dinner at Lilia — Sat 8:00 PM".
- Use plain text (no markdown headers). Emoji sparingly.
- Messages are prefixed with the sender's name so you know who's talking."""


# ---------------------------------------------------------------------------
# Claude tool-use loop
# ---------------------------------------------------------------------------


def ask_claude(chat_id: int, user_text: str) -> str:
    HISTORY[chat_id].append({"role": "user", "content": user_text})
    messages = list(HISTORY[chat_id])

    for _ in range(MAX_TOOL_ROUNDS):
        response = claude.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=system_prompt(),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            HISTORY[chat_id].append({"role": "assistant", "content": text or "…"})
            return text

        # Execute every requested tool, feed results back
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                log.info("tool %s %s", block.name, block.input)
                output = cal.run_tool(block.name, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
        messages.append({"role": "user", "content": results})

    return "Sorry — that took too many steps. Try rephrasing?"


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


def _authorized(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == ALLOWED_CHAT_ID


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update) or not update.message or not update.message.text:
        return

    msg = update.message
    text = msg.text.strip()

    if not RESPOND_TO_ALL:
        me = context.bot.username or ""
        mentioned = f"@{me}".lower() in text.lower()
        is_reply_to_bot = (
            msg.reply_to_message
            and msg.reply_to_message.from_user
            and msg.reply_to_message.from_user.id == context.bot.id
        )
        if not (mentioned or is_reply_to_bot):
            return
        text = text.replace(f"@{me}", "").strip()

    sender = msg.from_user.first_name if msg.from_user else "Someone"
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    try:
        reply = ask_claude(msg.chat_id, f"{sender}: {text}")
    except Exception:
        log.exception("Claude call failed")
        await msg.reply_text("Hit an error talking to my brain 🧠 — try again in a sec.")
        return

    if reply and reply != "PASS":
        await msg.reply_text(reply)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await update.message.reply_text(
            f"This chat isn't authorized. Chat ID: {update.effective_chat.id}"
        )
        return
    await update.message.reply_text(
        "Hey! I manage your shared calendar. Just tell me things like:\n\n"
        "• dinner at Lilia saturday 8pm\n"
        "• dentist tuesday 10am for me\n"
        "• what do we have this weekend?\n"
        "• move friday dinner to 7:30\n\n"
        "Commands: /weekend  /week  /today"
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper for setup: reports this chat's ID."""
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


def _digest_prompt(label: str, start: datetime, end: datetime) -> str:
    return (
        f"System task: post the {label}. Use list_events from "
        f"{start.isoformat()} to {end.isoformat()}, then write a short, warm summary "
        f"for the group. If nothing is scheduled, say so cheerfully and maybe suggest "
        f"it's a free stretch. Do not reply PASS."
    )


def _weekend_window(now: datetime) -> tuple[datetime, datetime]:
    days_to_friday = (4 - now.weekday()) % 7
    friday = (now + timedelta(days=days_to_friday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return friday, friday + timedelta(days=3)  # Fri 00:00 -> Mon 00:00


async def cmd_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    now = datetime.now(TZ)
    start, end = _weekend_window(now)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = ask_claude(update.effective_chat.id, _digest_prompt("weekend preview", start, end))
    await update.message.reply_text(reply)


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = ask_claude(update.effective_chat.id, _digest_prompt("week-ahead summary", start, start + timedelta(days=7)))
    await update.message.reply_text(reply)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = ask_claude(update.effective_chat.id, _digest_prompt("today summary", start, start + timedelta(days=1)))
    await update.message.reply_text(reply)


# ---------------------------------------------------------------------------
# Scheduled digests
# ---------------------------------------------------------------------------


async def scheduled_digest(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily; decides based on weekday whether a digest is due."""
    now = datetime.now(TZ)
    job_name = context.job.data

    if job_name == "weekend" and now.weekday() == 4:  # Friday
        start, end = _weekend_window(now)
        label = "weekend preview"
    elif job_name == "week_ahead" and now.weekday() == 6:  # Sunday
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        end = start + timedelta(days=7)
        label = "week-ahead summary"
    else:
        return

    try:
        reply = ask_claude(ALLOWED_CHAT_ID, _digest_prompt(label, start, end))
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=reply)
    except Exception:
        log.exception("Scheduled digest failed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("weekend", cmd_weekend))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # Daily jobs; the handler checks the weekday itself (Friday 9am / Sunday 6pm)
    app.job_queue.run_daily(scheduled_digest, time=time(9, 0, tzinfo=TZ), data="weekend")
    app.job_queue.run_daily(scheduled_digest, time=time(18, 0, tzinfo=TZ), data="week_ahead")

    log.info("Bot starting (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
