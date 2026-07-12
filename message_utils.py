"""Safe boundaries between Telegram metadata and model-visible messages."""


def build_user_turn(message_text: str, sender_display_name: str = "") -> dict:
    """Build a Claude user turn without exposing mutable Telegram profile data.

    Telegram display names are user-controlled metadata and may look like natural-language
    instructions. Keep them out of model-visible content so only the message body can cause
    an action. The argument remains explicit to make accidental reintroduction harder.
    """
    del sender_display_name
    return {"role": "user", "content": message_text}


def visible_reply_text(reply: str) -> str | None:
    """Return Telegram-safe Claude output, suppressing the internal PASS sentinel."""
    text = (reply or "").strip()
    if not text or text.casefold() == "pass":
        return None
    return text
