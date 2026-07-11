"""
Personal Assistant Bot
----------------------
A Telegram bot powered by Claude with pluggable capabilities.

Current capabilities:
  - Google Calendar management
  - Tempo / MPP stablecoin-powered API services

Talk to it in plain English:
  "dinner at Lilia saturday 8pm"
  "what do we have this weekend?"
  "use Parallel to generate an image of a sunset"
  "what's my Tempo wallet balance?"
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

from calendar_client import TOOLS as CALENDAR_TOOLS, CalendarClient
from message_utils import build_user_turn
from payment_approval import PendingPaymentApproval
from tempo_client import TEMPO_TOOLS, TempoClient, TempoRequestBudget

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
# httpx logs full Telegram API URLs, which contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("assistant-bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
BOT_OWNER = os.environ.get("BOT_OWNER", os.environ.get("COUPLE_NAMES", "Ezra"))
RESPOND_TO_ALL = os.environ.get("RESPOND_TO_ALL", "true").lower() == "true"

TZ = ZoneInfo(TIMEZONE)

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
cal = CalendarClient()
tempo = TempoClient()

ALL_TOOLS = CALENDAR_TOOLS + TEMPO_TOOLS

# Rolling conversation memory per chat (survives until process restart)
HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=40))
PENDING_TEMPO_APPROVALS: dict[int, PendingPaymentApproval] = {}

MAX_TOOL_ROUNDS = 10


def system_prompt(approved_call: dict = None) -> str:
    now = datetime.now(TZ)
    return f"""You are a personal AI assistant for {BOT_OWNER}, living in a private Telegram group.

Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} ({TIMEZONE}).

You have the following capabilities — use whichever tools fit the request:

CALENDAR
- Add events when plans, reservations, appointments, or trips are mentioned. Infer sensible defaults (dinner = 2h, appointments = 1h). Resolve relative dates ("saturday", "next friday") against today.
- Answer schedule questions by listing events first, then summarizing.
- Update or delete events when asked. If ambiguous, list matches and ask which one.

TEMPO / PAID APIS
- Access external APIs and services via Tempo (stablecoin-powered). When asked to use a service (image generation, web search, Parallel, browser automation, etc.):
  1. Use tempo_discover_services to find the right service
  2. Use tempo_service_details to get the exact URL, endpoint, and body schema
  3. Use tempo_call_service to call it — never guess endpoint paths
- Prefer fixed-price $0.01 search/extract endpoints for ordinary requests.
- Use dynamic task/research endpoints only when the user explicitly requests deeper research.
- If a tool returns confirmation_required, tell the user the exact confirmation phrase and stop.
- Never retry a paid call after it has been submitted, even if its response is an error.
- Task status polling is free; poll the exact run URL returned by the submitted task.
- Use tempo_wallet_balance to check balance when asked.

GENERAL
- Answer questions, help think through problems, draft messages, do research, etc.
- If a message is clearly not directed at you (just conversation between people), reply with exactly: PASS

Style: conversational and direct. Confirm tool actions in one line. Plain text only, no markdown headers. Emoji sparingly.""" + (
        "\n\nPAYMENT APPROVAL\n"
        "The current user message approved exactly one previously blocked Tempo call. "
        "Reissue that call with exactly these arguments; do not alter or add paid calls: "
        + json.dumps(approved_call, sort_keys=True)
        if approved_call
        else ""
    )


# ---------------------------------------------------------------------------
# Claude tool-use loop
# ---------------------------------------------------------------------------


def ask_claude(
    chat_id: int, user_text: str, sender_display_name: str = ""
) -> str:
    pending = PENDING_TEMPO_APPROVALS.get(chat_id)
    approved_call = None
    approved_limit = ""
    if pending and pending.expired():
        PENDING_TEMPO_APPROVALS.pop(chat_id, None)
        pending = None
    if pending and pending.matches(user_text):
        approved_call = pending.tool_args
        approved_limit = pending.amount
        PENDING_TEMPO_APPROVALS.pop(chat_id, None)

    request_budget = TempoRequestBudget(
        auto_limit=tempo.auto_spend,
        approved_call=approved_call,
        approved_limit=approved_limit,
    )
    HISTORY[chat_id].append(build_user_turn(user_text, sender_display_name))
    messages = list(HISTORY[chat_id])

    for _ in range(MAX_TOOL_ROUNDS):
        response = claude.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system_prompt(approved_call),
            tools=ALL_TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            HISTORY[chat_id].append({"role": "assistant", "content": text or "…"})
            return text

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                log.info("tool %s %s", block.name, block.input)
                if block.name.startswith("tempo_"):
                    tool_args = dict(block.input)
                    output = tempo.run_tool(
                        block.name,
                        tool_args,
                        request_budget=request_budget,
                    )
                    approval = PendingPaymentApproval.from_tool_result(
                        tool_args, output
                    )
                    if approval:
                        PENDING_TEMPO_APPROVALS[chat_id] = approval
                else:
                    output = cal.run_tool(block.name, dict(block.input))
                log.info("tool %s result: %s", block.name, output[:200])
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

    sender_display_name = msg.from_user.first_name if msg.from_user else ""
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    try:
        reply = ask_claude(
            msg.chat_id,
            text,
            sender_display_name=sender_display_name,
        )
    except Exception:
        log.exception("Claude call failed")
        await msg.reply_text("Hit an error — try again in a sec.")
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
        f"Hey {BOT_OWNER}! I'm your personal assistant. I can:\n\n"
        "• Manage your calendar — \"dinner at Lilia saturday 8pm\"\n"
        "• Use Tempo services — \"generate an image of a sunset\"\n"
        "• Answer questions and help with anything else\n\n"
        "Commands: /weekend  /week  /today  /balance"
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = ask_claude(update.effective_chat.id, "System: show my Tempo wallet balance.")
    await update.message.reply_text(reply)


def _digest_prompt(label: str, start: datetime, end: datetime) -> str:
    return (
        f"System task: post the {label}. Use list_events from "
        f"{start.isoformat()} to {end.isoformat()}, then write a short, warm summary. "
        f"If nothing is scheduled, say so cheerfully. Do not reply PASS."
    )


def _weekend_window(now: datetime) -> tuple[datetime, datetime]:
    days_to_friday = (4 - now.weekday()) % 7
    friday = (now + timedelta(days=days_to_friday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return friday, friday + timedelta(days=3)


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
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("weekend", cmd_weekend))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    app.job_queue.run_daily(scheduled_digest, time=time(9, 0, tzinfo=TZ), data="weekend")
    app.job_queue.run_daily(scheduled_digest, time=time(18, 0, tzinfo=TZ), data="week_ahead")

    log.info("Bot starting (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
