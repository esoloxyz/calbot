"""Validated, immediate execution of calendar mutations."""

from __future__ import annotations

import json
import logging

from calbot.assistant.execution import ToolExecutionResult
from calbot.assistant.postconditions import calendar_action_reply
from calbot.calendar.contracts import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    CALENDAR_MUTATION_TOOLS,
    CALENDAR_REQUIRED_FIELDS,
)


log = logging.getLogger("assistant-bot")
MAX_CALENDAR_BATCH_ACTIONS = 5


class CalendarMutationExecutor:
    """Validate, version-check, execute, and summarize calendar writes."""

    def __init__(self, calendar_client, *, logger=None):
        self.calendar = calendar_client
        self.log = logger or log

    @staticmethod
    def _validated(name: str, args: dict) -> dict:
        if name not in CALENDAR_MUTATION_TOOLS:
            raise ValueError("Unsupported calendar mutation")
        if not isinstance(args, dict):
            raise ValueError("Calendar tool arguments must be an object")

        allowed = set(CALENDAR_MUTATION_FIELDS[name])
        unsupported = set(args) - allowed
        if unsupported:
            raise ValueError("Calendar change contains unsupported fields")

        for field_name in CALENDAR_REQUIRED_FIELDS[name]:
            if field_name not in args:
                raise ValueError(f"Calendar change is missing {field_name}")

        validated = {}
        for field_name, value in args.items():
            if field_name == "all_day":
                if type(value) is not bool:
                    raise ValueError("all_day must be true or false")
                validated[field_name] = value
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")
            if field_name in CALENDAR_REQUIRED_FIELDS[name] and not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            limit = CALENDAR_FIELD_LIMITS.get(field_name)
            if limit is not None and len(value) > limit:
                raise ValueError(f"{field_name} is too long")
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
        )
