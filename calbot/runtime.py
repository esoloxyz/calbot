"""Calendar-only Calbot runtime with immediate, verified calendar writes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from calbot.assistant.execution import ToolExecutionResult
from calbot.assistant.loop import run_assistant_turn
from calbot.assistant.policy import CALENDAR_ASSISTANT_POLICY
from calbot.assistant.postconditions import (
    CALENDAR_MUTATION_TOOLS,
    calendar_action_reply,
)
from calbot.calendar.client import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    CALENDAR_REQUIRED_FIELDS,
)
from calbot.messages import build_user_turn


log = logging.getLogger("assistant-bot")
MAX_HISTORY_TURNS = 12
MAX_CALENDAR_BATCH_ACTIONS = 5


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str = field(repr=False)
    anthropic_api_key: str = field(repr=False)
    allowed_chat_id: int
    timezone: str = "America/New_York"
    model: str = "claude-sonnet-4-6"
    bot_owner: str = "there"
    respond_to_all: bool = True
    google_service_account_json: str = field(default="", repr=False)
    calendar_id: str = ""
    allowed_user_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "BotConfig":
        values = os.environ if env is None else env
        required = (
            "TELEGRAM_BOT_TOKEN",
            "ANTHROPIC_API_KEY",
            "ALLOWED_CHAT_ID",
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "CALENDAR_ID",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        try:
            allowed_chat_id = int(values["ALLOWED_CHAT_ID"])
        except ValueError as exc:
            raise ValueError("ALLOWED_CHAT_ID must be an integer") from exc

        timezone = values.get("TIMEZONE", "America/New_York")
        try:
            ZoneInfo(timezone)
        except Exception as exc:
            raise ValueError(
                f"TIMEZONE is not a valid IANA timezone: {timezone}"
            ) from exc

        respond_to_all = values.get("RESPOND_TO_ALL", "true").casefold()
        if respond_to_all not in {"true", "false"}:
            raise ValueError("RESPOND_TO_ALL must be true or false")

        try:
            allowed_users = frozenset(
                int(value.strip())
                for value in values.get("ALLOWED_USER_IDS", "").split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise ValueError("ALLOWED_USER_IDS must contain only integers") from exc

        return cls(
            telegram_token=values["TELEGRAM_BOT_TOKEN"],
            anthropic_api_key=values["ANTHROPIC_API_KEY"],
            allowed_chat_id=allowed_chat_id,
            timezone=timezone,
            model=values.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            bot_owner=values.get("BOT_OWNER", "there"),
            respond_to_all=respond_to_all == "true",
            google_service_account_json=values["GOOGLE_SERVICE_ACCOUNT_JSON"],
            calendar_id=values["CALENDAR_ID"],
            allowed_user_ids=allowed_users,
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def actor_allowed(self, user_id: int) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids


class BlockingBridge:
    """Run synchronous clients off-loop, one operation at a time."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def run(self, function: Callable, *args, **kwargs):
        async with self._lock:
            worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError as cancellation:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                try:
                    worker.result()
                except Exception:
                    log.exception(
                        "Blocking operation failed after its caller was cancelled"
                    )
                raise cancellation


class BotRuntime:
    """Synchronous assistant core called by the Telegram adapter."""

    def __init__(
        self,
        *,
        config: BotConfig,
        claude_client,
        calendar_client,
        tools: list,
        max_tool_rounds: int = 8,
    ):
        self.config = config
        self.claude = claude_client
        self.cal = calendar_client
        self.tools = list(tools)
        self.max_tool_rounds = max_tool_rounds
        self.history: dict[int, deque] = defaultdict(deque)

    def _record_history_turn(
        self,
        chat_id: int,
        user_message: dict,
        assistant_message: dict,
    ) -> None:
        history = self.history[chat_id]
        history.extend((user_message, assistant_message))
        while len(history) > MAX_HISTORY_TURNS * 2:
            history.popleft()
            history.popleft()

    def system_prompt(self) -> str:
        now = datetime.now(self.config.tz)
        return f"""You are Calbot, a private shared-calendar assistant for {self.config.bot_owner}.

Current date and time: {now.strftime("%A, %B %d, %Y at %I:%M %p")}
Timezone: {self.config.timezone}

Your scope is intentionally narrow: help the two people in this private Telegram
chat view and manage their shared Google Calendar. Do not claim you can search the
web, make payments, order food, manage wallets, or call any non-calendar service.

{CALENDAR_ASSISTANT_POLICY}"""

    @staticmethod
    def _validated_mutation(name: str, args: dict) -> dict:
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

    def _prepare_action(
        self,
        name: str,
        args: dict,
        *,
        request_id: str,
    ) -> dict:
        validated = self._validated_mutation(name, args)
        preview = self.cal.preview_mutation(name, validated)
        execution_args = dict(validated)
        if name == "create_event":
            # The calendar client may repair a same-date midnight end into the
            # following day while validating the preview.
            execution_args.update(preview["event"])
            execution_args["_idempotency_key"] = request_id
        else:
            execution_args["_expected_etag"] = preview["event_etag"]
        return {
            "name": name,
            "args": execution_args,
            "preview": preview,
        }

    def _execute_actions(
        self,
        *,
        actions: list[tuple[str, dict]],
        request_id: str,
    ) -> ToolExecutionResult:
        if not actions or len(actions) > MAX_CALENDAR_BATCH_ACTIONS:
            return ToolExecutionResult(
                output=json.dumps({"error": "Too many calendar changes"}),
                user_reply=(
                    f"Please limit one request to {MAX_CALENDAR_BATCH_ACTIONS} "
                    "calendar changes."
                ),
                halt=True,
            )

        replies = []
        outcomes = []
        for index, (name, args) in enumerate(actions, start=1):
            try:
                action = self._prepare_action(
                    name,
                    args,
                    request_id=f"{request_id}:{index}",
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning(
                    "Calendar action rejected before write "
                    "(action=%s index=%s count=%s): %s",
                    name,
                    index,
                    len(actions),
                    exc,
                )
                replies.append(
                    "I couldn't make one calendar change because its date or time "
                    "didn't make sense. Please ask me to try that one again."
                )
                outcomes.append("validation_failed")
                continue
            except Exception:
                log.exception(
                    "Calendar action preparation failed (action=%s index=%s count=%s)",
                    name,
                    index,
                    len(actions),
                )
                replies.append(
                    "I couldn't load the calendar details needed for one change. "
                    "Please ask me to try that one again."
                )
                outcomes.append("preparation_failed")
                continue

            log.info(
                "calendar action started (action=%s index=%s count=%s)",
                name,
                index,
                len(actions),
            )
            output = self.cal.run_tool(name, action["args"])
            reply = calendar_action_reply(
                name,
                self._reply_args(action),
                output,
            )
            replies.append(reply or "I couldn't verify that calendar change.")
            try:
                result = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                result = {}
            status = result.get("status") if isinstance(result, dict) else None
            outcomes.append(status or "failed")
            log.info(
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

    @staticmethod
    def _reply_args(action: dict) -> dict:
        args = dict(action["args"])
        preview = action["preview"]
        if action["name"] != "create_event":
            args["title"] = preview.get("current_event", {}).get("title", "the event")
        return args

    def ask(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_text: str,
        sender_display_name: str = "",
        request_id: str = "",
    ) -> str:
        user_turn = build_user_turn(user_text, sender_display_name)
        messages = list(self.history[chat_id]) + [user_turn]
        stable_request_id = request_id or f"telegram:{chat_id}:{user_id}"

        def run_tool(name: str, args: dict):
            if name in CALENDAR_MUTATION_TOOLS:
                return self._execute_actions(
                    actions=[(name, args)],
                    request_id=stable_request_id,
                )
            if name != "list_events":
                return json.dumps(
                    {
                        "error": "Unsupported tool",
                        "error_code": "unsupported_tool",
                    }
                )
            return self.cal.run_tool(name, args)

        def run_tool_batch(actions: list[tuple[str, dict]]):
            return self._execute_actions(
                actions=actions,
                request_id=stable_request_id,
            )

        text = run_assistant_turn(
            claude_client=self.claude,
            model=self.config.model,
            system_prompt=self.system_prompt(),
            tools=self.tools,
            messages=messages,
            run_tool=run_tool,
            run_tool_batch=run_tool_batch,
            max_tool_rounds=self.max_tool_rounds,
            logger=log,
        )
        self._record_history_turn(
            chat_id,
            user_turn,
            {"role": "assistant", "content": text or "…"},
        )
        return text
