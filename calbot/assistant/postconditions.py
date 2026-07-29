"""Small, deterministic calendar-result boundaries."""

from __future__ import annotations

import json
import re


CALENDAR_MUTATION_TOOLS = frozenset({"create_event", "update_event", "delete_event"})
_UNVERIFIED_COMPLETION = re.compile(
    r"(?:\b(?:i|we)(?:['’]ve| have)?\s+"
    r"(?:added|created|scheduled|updated|changed|deleted|removed|moved)\b"
    r"|^\s*(?:done|all set)\b)",
    re.IGNORECASE,
)


def claims_calendar_success(text: str) -> bool:
    """Return whether model prose claims that it completed a calendar write."""
    return bool(_UNVERIFIED_COMPLETION.search(text or ""))


def calendar_action_reply(name: str, args: dict, output: str) -> str | None:
    """Render a write result from executor data rather than model prose."""
    if name not in CALENDAR_MUTATION_TOOLS:
        return None

    title = str(args.get("title") or "the event")
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        result = {"error": "the calendar returned an unreadable response"}
    if not isinstance(result, dict):
        result = {"error": "the calendar returned an unreadable response"}

    error = result.get("error")
    if error:
        verbs = {
            "create_event": "add",
            "update_event": "update",
            "delete_event": "delete",
        }
        destination = " to the calendar" if name == "create_event" else ""
        retry = (
            " It changed while I was working on it, so please ask me again."
            if result.get("error_code") == "event_changed_before_write"
            else " Please try again."
        )
        return f"I couldn't {verbs[name]} {title}{destination}.{retry}"

    status = result.get("status")
    if name == "create_event" and status == "duplicate":
        return f"That's already on the calendar: {result.get('title') or title}."
    if name == "create_event" and status == "created":
        return f"Done — {title} is on the calendar."
    if name == "update_event" and status == "updated":
        return f"Done — {title} was updated."
    if name == "delete_event" and status == "deleted":
        return f"Done — {title} was deleted."

    return (
        f"I couldn't verify the calendar result for {title}, so I didn't claim "
        "it succeeded."
    )
