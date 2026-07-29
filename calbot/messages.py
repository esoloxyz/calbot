"""Safe boundaries between Telegram metadata and model-visible messages."""

from __future__ import annotations

import logging
import re


log = logging.getLogger("assistant-bot")
_CONVERSATIONAL_FALLBACK = (
    "I couldn't turn that into a clear calendar answer. Please ask me again."
)
_NON_CONVERSATIONAL_OUTPUT = re.compile(
    r"(?:"
    r"`"
    r"|^\s*[\{\[]"
    r"|[\}\]]\s*$"
    r"|[\"'][A-Za-z_][A-Za-z0-9_ -]*[\"']\s*:"
    r"|(?:^|\n)\s*(?:action|status|error|event_id|tool|arguments?)\s*:"
    r"|\b(?:create_event|update_event|delete_event|list_events|event_id|"
    r"time_min|time_max|tool_use_id|error_code)\b"
    r"|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?"
    r"|(?:^|\n)\s*(?:def|class|import|from|function|const|let|var)\s+"
    r"|(?:^|\n)\s*\$\s+"
    r"|\b(?:print|console\.log)\s*\("
    r"|(?:^|\n)\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^\n]+"
    r"|</?[A-Za-z][^>]*>"
    r"|\bTraceback\s*\("
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def build_user_turn(message_text: str, sender_display_name: str = "") -> dict:
    """Build a Claude user turn without exposing mutable Telegram profile data.

    Telegram display names are user-controlled metadata and may look like natural-language
    instructions. Keep them out of model-visible content so only the message body can cause
    an action. The argument remains explicit to make accidental reintroduction harder.
    """
    del sender_display_name
    return {"role": "user", "content": message_text}


def visible_reply_text(reply: str) -> str | None:
    """Return conversational Telegram text and suppress internal representations."""
    text = (reply or "").strip()
    if not text or text.casefold() == "pass":
        return None
    if _NON_CONVERSATIONAL_OUTPUT.search(text):
        log.warning("Suppressed non-conversational assistant output")
        return _CONVERSATIONAL_FALLBACK
    return text
