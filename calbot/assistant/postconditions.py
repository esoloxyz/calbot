"""Small, deterministic calendar-result boundaries."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from calbot.calendar.contracts import CALENDAR_MUTATION_TOOLS

_UNVERIFIED_COMPLETION = re.compile(
    r"(?:\b(?:i|we)(?:['’]ve| have)?\s+"
    r"(?:added|created|scheduled|updated|changed|deleted|removed|moved)\b"
    r"|^\s*(?:done|all set)\b)",
    re.IGNORECASE,
)
_CALENDAR_STATE_CLAIM = re.compile(
    r"(?:"
    r"\b(?:already\s+)?(?:on|in)\s+(?:the|your|our)\s+calendar\b|"
    r"\b(?:scheduled|booked)\s+(?:for|on|at)\b"
    r")",
    re.IGNORECASE,
)


def claims_calendar_success(text: str) -> bool:
    """Return whether model prose claims that it completed a calendar write."""
    return bool(_UNVERIFIED_COMPLETION.search(text or ""))


def claims_calendar_state(text: str) -> bool:
    """Return whether prose asserts that a concrete event is scheduled."""
    return bool(_CALENDAR_STATE_CLAIM.search(text or ""))


def _friendly_date(value: date) -> str:
    return f"{value.strftime('%A, %B')} {value.day}".lower()


def _friendly_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    minute = f":{value.minute:02d}" if value.minute else ""
    suffix = "am" if value.hour < 12 else "pm"
    return f"{hour}{minute}{suffix}"


def _friendly_schedule(args: dict) -> str:
    """Render validated calendar bounds as concise conversational prose."""
    start = args.get("start")
    end = args.get("end")
    if not isinstance(start, str) or not start:
        return ""

    try:
        if args.get("all_day") or "T" not in start:
            start_date = date.fromisoformat(start)
            if not isinstance(end, str) or not end:
                return _friendly_date(start_date)
            # Google Calendar stores the end of an all-day event exclusively.
            inclusive_end = date.fromisoformat(end) - timedelta(days=1)
            if inclusive_end <= start_date:
                return _friendly_date(start_date)
            return (
                f"{_friendly_date(start_date)} through {_friendly_date(inclusive_end)}"
            )

        start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if not isinstance(end, str) or not end:
            return (
                f"{_friendly_date(start_time.date())} at {_friendly_time(start_time)}"
            )
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if end_time.date() == start_time.date():
            return (
                f"{_friendly_date(start_time.date())} from "
                f"{_friendly_time(start_time)} to {_friendly_time(end_time)}"
            )
        return (
            f"{_friendly_date(start_time.date())} at {_friendly_time(start_time)} "
            f"through {_friendly_date(end_time.date())} at {_friendly_time(end_time)}"
        )
    except ValueError:
        return ""


def calendar_action_reply(name: str, args: dict, output: str) -> str | None:
    """Render a write result from executor data rather than model prose."""
    if name not in CALENDAR_MUTATION_TOOLS:
        return None

    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        result = {"error": "the calendar returned an unreadable response"}
    if not isinstance(result, dict):
        result = {"error": "the calendar returned an unreadable response"}

    final_event = dict(args)
    for field in ("title", "start", "end", "all_day"):
        if field in result:
            final_event[field] = result[field]
    title = str(final_event.get("title") or "the event").lower()
    schedule = _friendly_schedule(final_event)
    timing = f" for {schedule}" if schedule else ""

    error = result.get("error")
    if error:
        verbs = {
            "create_event": "add",
            "update_event": "update",
            "delete_event": "delete",
        }
        destination = " to the calendar" if name == "create_event" else ""
        retry = (
            "it changed while i was working on it, so please ask me again."
            if result.get("error_code") == "event_changed_before_write"
            else "please try again."
        )
        return f"i couldn't {verbs[name]} {title}{destination}. {retry}"

    status = result.get("status")
    if name == "create_event" and status == "duplicate":
        return f"that's already on the calendar: {title}{timing}."
    if name == "create_event" and status == "created":
        return f"done. {title} is on the calendar{timing}."
    if name == "update_event" and status == "updated":
        return f"done. {title} was updated{timing}."
    if name == "delete_event" and status == "deleted":
        location = f" from {schedule}" if schedule else ""
        return f"done. {title} was deleted{location}."

    return (
        f"i couldn't verify the calendar result for {title}, so i didn't claim "
        "it succeeded."
    )
