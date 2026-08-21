"""Validated, immediate execution of calendar mutations."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calbot.assistant.execution import ToolExecutionResult
from calbot.assistant.postconditions import calendar_action_reply
from calbot.calendar.contracts import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    CALENDAR_MUTATION_TOOLS,
    CALENDAR_REQUIRED_FIELDS,
    MAX_EVENT_ATTENDEES,
    MAX_EVENT_RECURRENCE_LINES,
    MAX_EVENT_REMINDERS,
)


log = logging.getLogger("assistant-bot")
MAX_CALENDAR_BATCH_ACTIONS = 5
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ENUM_FIELDS = {
    "transparency": {"opaque", "transparent"},
    "visibility": {"default", "public", "private", "confidential"},
    "event_status": {"confirmed", "tentative"},
    "send_updates": {"none", "all", "externalOnly"},
    "recurrence_scope": {"this_event", "entire_series"},
}
_BOOLEAN_FIELDS = {"all_day", "create_google_meet"}
_TWO_HOUR_EVENT = re.compile(
    r"\b(?:breakfast|brunch|lunch|dinner|drinks?|date|reservation)\b",
    re.IGNORECASE,
)


class CalendarMutationExecutor:
    """Validate, version-check, execute, and summarize calendar writes."""

    def __init__(self, calendar_client, *, logger=None):
        self.calendar = calendar_client
        self.log = logger or log

    @staticmethod
    def _default_end(title: str, start: str, all_day: bool) -> str:
        """Apply stable product defaults when the user omitted an end time."""
        if all_day or "T" not in start:
            return (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
        minutes = 120 if _TWO_HOUR_EVENT.search(title) else 60
        return (parsed + timedelta(minutes=minutes)).isoformat()

    @staticmethod
    def _validated(name: str, args: dict) -> dict:
        if name not in CALENDAR_MUTATION_TOOLS:
            raise ValueError("Unsupported calendar mutation")
        if not isinstance(args, dict):
            raise ValueError("Calendar tool arguments must be an object")

        normalized_args = dict(args)
        if name == "create_event":
            start = normalized_args.get("start")
            title = normalized_args.get("title")
            if isinstance(start, str) and "all_day" not in normalized_args:
                normalized_args["all_day"] = "T" not in start
            if (
                "end" not in normalized_args
                and isinstance(title, str)
                and isinstance(start, str)
            ):
                normalized_args["end"] = CalendarMutationExecutor._default_end(
                    title,
                    start,
                    bool(normalized_args.get("all_day", False)),
                )

        allowed = set(CALENDAR_MUTATION_FIELDS[name])
        unsupported = set(normalized_args) - allowed
        if unsupported:
            raise ValueError("Calendar change contains unsupported fields")

        for field_name in CALENDAR_REQUIRED_FIELDS[name]:
            if field_name not in normalized_args:
                raise ValueError(f"Calendar change is missing {field_name}")

        validated = {}
        for field_name, value in normalized_args.items():
            if field_name in _BOOLEAN_FIELDS:
                if type(value) is not bool:
                    raise ValueError(f"{field_name} must be true or false")
                validated[field_name] = value
                continue
            if field_name in {"recurrence", "attendees", "reminder_minutes"}:
                if not isinstance(value, list):
                    raise ValueError(f"{field_name} must be a list")
                if field_name == "recurrence":
                    if len(value) > MAX_EVENT_RECURRENCE_LINES or not all(
                        isinstance(item, str)
                        and len(item) <= 500
                        and item.startswith(("RRULE:", "RDATE:", "EXDATE:"))
                        for item in value
                    ):
                        raise ValueError("recurrence contains invalid lines")
                elif field_name == "attendees":
                    if len(value) > MAX_EVENT_ATTENDEES or not all(
                        isinstance(item, str)
                        and len(item) <= 320
                        and _EMAIL.fullmatch(item)
                        for item in value
                    ):
                        raise ValueError("attendees contains invalid email addresses")
                elif len(value) > MAX_EVENT_REMINDERS or not all(
                    type(item) is int and 0 <= item <= 40320 for item in value
                ):
                    raise ValueError("reminder_minutes contains invalid offsets")
                validated[field_name] = list(value)
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")
            if field_name in CALENDAR_REQUIRED_FIELDS[name] and not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            if field_name == "title" and not value.strip():
                raise ValueError("title must not be empty")
            limit = CALENDAR_FIELD_LIMITS.get(field_name)
            if limit is not None and len(value) > limit:
                raise ValueError(f"{field_name} is too long")
            if field_name in _ENUM_FIELDS and value not in _ENUM_FIELDS[field_name]:
                raise ValueError(f"{field_name} is invalid")
            if field_name == "event_timezone":
                try:
                    ZoneInfo(value)
                except (ValueError, ZoneInfoNotFoundError) as exc:
                    raise ValueError("event_timezone must be an IANA timezone") from exc
            if field_name == "source_url" and value:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("source_url must be an http or https URL")
            validated[field_name] = value
        return validated

    def _prepare(self, name: str, args: dict, *, request_id: str) -> dict:
        validated = self._validated(name, args)
        preview = self.calendar.preview_mutation(name, validated)
        execution_args = dict(validated)
        if name == "create_event":
            # Preview validation may repair a same-date midnight end.
            execution_args.update(preview["event"])
            execution_args["_idempotency_key"] = request_id
        else:
            if preview.get("resolved_event_id"):
                execution_args["event_id"] = preview["resolved_event_id"]
            execution_args["_expected_etag"] = preview["event_etag"]
        return {
            "name": name,
            "args": execution_args,
            "preview": preview,
        }

    @staticmethod
    def _reply_args(action: dict) -> dict:
        args = dict(action["args"])
        if action["name"] == "create_event":
            return args

        current_event = action["preview"].get("current_event", {})
        args.setdefault("title", current_event.get("title", "the event"))
        args.setdefault("start", current_event.get("start", ""))
        args.setdefault("end", current_event.get("end", ""))
        args.setdefault("all_day", "T" not in str(args.get("start", "")))
        return args

    def execute(
        self,
        *,
        actions: list[tuple[str, dict]],
        request_id: str,
    ) -> ToolExecutionResult:
        if not actions or len(actions) > MAX_CALENDAR_BATCH_ACTIONS:
            return ToolExecutionResult(
                output=json.dumps({"error": "Too many calendar changes"}),
                user_reply=(
                    f"please limit one request to {MAX_CALENDAR_BATCH_ACTIONS} "
                    "calendar changes."
                ),
                halt=True,
            )

        replies = []
        outcomes = []
        receipts = []
        for index, (name, args) in enumerate(actions, start=1):
            try:
                action = self._prepare(
                    name,
                    args,
                    request_id=f"{request_id}:{index}",
                )
            except (KeyError, TypeError, ValueError) as exc:
                self.log.warning(
                    "Calendar action rejected before write "
                    "(action=%s index=%s count=%s): %s",
                    name,
                    index,
                    len(actions),
                    exc,
                )
                replies.append(
                    "i couldn't make one calendar change because its date or time "
                    "didn't make sense. please ask me to try that one again."
                )
                outcomes.append("validation_failed")
                receipts.append(
                    {
                        "action": name,
                        "status": "validation_failed",
                        "title": str(args.get("title", "")),
                        "start": str(args.get("start", "")),
                        "end": str(args.get("end", "")),
                    }
                )
                continue
            except Exception:
                self.log.exception(
                    "Calendar action preparation failed (action=%s index=%s count=%s)",
                    name,
                    index,
                    len(actions),
                )
                replies.append(
                    "i couldn't load the calendar details needed for one change. "
                    "please ask me to try that one again."
                )
                outcomes.append("preparation_failed")
                receipts.append(
                    {
                        "action": name,
                        "status": "preparation_failed",
                        "title": str(args.get("title", "")),
                        "start": str(args.get("start", "")),
                        "end": str(args.get("end", "")),
                    }
                )
                continue

            self.log.info(
                "calendar action started (action=%s index=%s count=%s)",
                name,
                index,
                len(actions),
            )
            output = self.calendar.run_tool(name, action["args"])
            reply = calendar_action_reply(name, self._reply_args(action), output)
            replies.append(reply or "i couldn't verify that calendar change.")
            try:
                result = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                result = {}
            status = result.get("status") if isinstance(result, dict) else None
            outcomes.append(status or "failed")
            receipt_args = self._reply_args(action)
            receipt_fields = {
                key: value
                for key, value in action["args"].items()
                if not key.startswith("_") and key != "event_id"
            }
            if "description" in receipt_fields:
                receipt_fields["description"] = str(receipt_fields["description"])[:500]
            receipts.append(
                {
                    "action": name,
                    "status": status or "failed",
                    "event_id": str(
                        result.get("id", receipt_args.get("event_id", ""))
                        if isinstance(result, dict)
                        else receipt_args.get("event_id", "")
                    ),
                    "title": str(
                        result.get("title", receipt_args.get("title", ""))
                        if isinstance(result, dict)
                        else receipt_args.get("title", "")
                    ),
                    "start": str(
                        result.get("start", receipt_args.get("start", ""))
                        if isinstance(result, dict)
                        else receipt_args.get("start", "")
                    ),
                    "end": str(
                        result.get("end", receipt_args.get("end", ""))
                        if isinstance(result, dict)
                        else receipt_args.get("end", "")
                    ),
                    "link": str(result.get("link", ""))
                    if isinstance(result, dict)
                    else "",
                    "fields": receipt_fields,
                }
            )
            self.log.info(
                "calendar action completed (action=%s index=%s count=%s outcome=%s)",
                name,
                index,
                len(actions),
                status or "failed",
            )

        return ToolExecutionResult(
            output=json.dumps({"status": "completed", "outcomes": outcomes}),
            user_reply="\n".join(replies),
            halt=True,
            receipts=tuple(receipts),
        )
