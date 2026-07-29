"""Calendar-only Calbot runtime with explicit approval boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
from calbot.authorization import PendingAction, PendingActionStore
from calbot.calendar.client import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    CALENDAR_REQUIRED_FIELDS,
)
from calbot.messages import build_user_turn


log = logging.getLogger("assistant-bot")
MAX_HISTORY_TURNS = 12
MAX_CALENDAR_BATCH_ACTIONS = 5
_APPROVAL_ATTEMPT = re.compile(r"^approve\b", re.IGNORECASE)


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
        self.approvals = PendingActionStore()

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

    @staticmethod
    def _preview_line(name: str, preview: dict) -> str:
        if name == "create_event":
            event = preview.get("event", {})
            title = event.get("title") or "(untitled event)"
            timing = f"{event.get('start', '?')} → {event.get('end', '?')}"
            location = f" at {event['location']}" if event.get("location") else ""
            return f"Add “{title}” ({timing}){location}"

        current = preview.get("current_event", {})
        title = current.get("title") or "(untitled event)"
        if name == "delete_event":
            return (
                f"Delete “{title}” "
                f"({current.get('start', '?')} → {current.get('end', '?')})"
            )

        changes = preview.get("changes", {})
        rendered = ", ".join(
            f"{key.replace('_', ' ')} → {value}" for key, value in changes.items()
        )
        return f"Update “{title}”: {rendered or 'no changes'}"

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
            execution_args["_idempotency_key"] = request_id
        else:
            execution_args["_expected_etag"] = preview["event_etag"]
        return {
            "name": name,
            "args": execution_args,
            "preview": preview,
        }

    def _propose_actions(
        self,
        *,
        actor: tuple[int, int],
        actions: list[tuple[str, dict]],
        request_id: str,
        request_text: str,
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

        try:
            prepared = [
                self._prepare_action(
                    name,
                    args,
                    request_id=f"{request_id}:{index}",
                )
                for index, (name, args) in enumerate(actions, start=1)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return ToolExecutionResult(
                output=json.dumps({"error": str(exc)}),
                user_reply=f"I couldn't prepare that calendar change: {exc}",
                halt=True,
            )
        except Exception:
            log.exception("Calendar preview failed")
            return ToolExecutionResult(
                output=json.dumps({"error": "Calendar preview failed"}),
                user_reply=(
                    "I couldn't load the calendar details needed to preview that "
                    "change. Please try again."
                ),
                halt=True,
            )

        lines = [
            self._preview_line(action["name"], action["preview"]) for action in prepared
        ]
        preview_text = "\n".join(f"• {line}" for line in lines)
        pending = self.approvals.propose(
            actor=actor,
            tool_name="_calendar_batch",
            tool_args={"actions": prepared},
            preview=preview_text,
            request_text=request_text[:4000],
        )
        label = (
            "Calendar change awaiting approval:"
            if len(prepared) == 1
            else f"{len(prepared)} calendar changes awaiting approval:"
        )
        return ToolExecutionResult(
            output=json.dumps({"status": "confirmation_required"}),
            user_reply=(
                f"{label}\n{pending.preview}\n\nReply approve to continue. "
                "Any other message cancels this."
            ),
            halt=True,
        )

    @staticmethod
    def _reply_args(action: dict) -> dict:
        args = dict(action["args"])
        preview = action["preview"]
        if action["name"] != "create_event":
            args["title"] = preview.get("current_event", {}).get("title", "the event")
        return args

    def _execute_approved(self, pending: PendingAction) -> str:
        actions = pending.tool_args.get("actions", [])
        replies = []
        for action in actions:
            output = self.cal.run_tool(action["name"], action["args"])
            reply = calendar_action_reply(
                action["name"],
                self._reply_args(action),
                output,
            )
            replies.append(reply or "I couldn't verify that calendar change.")
        return "\n".join(replies)

    def ask(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_text: str,
        sender_display_name: str = "",
        request_id: str = "",
    ) -> str:
        actor = (chat_id, user_id)
        pending = self.approvals.get(actor)
        if pending is not None:
            approved = self.approvals.resolve(actor, user_text)
            if approved is not None:
                reply = self._execute_approved(approved)
                self._record_history_turn(
                    chat_id,
                    {
                        "role": "user",
                        "content": "The user approved the pending calendar change.",
                    },
                    {"role": "assistant", "content": reply},
                )
                return reply
            if _APPROVAL_ATTEMPT.search(user_text.strip()):
                return (
                    "That approval was cancelled. When a change is pending, reply "
                    "with only: approve"
                )

        if user_text.strip().casefold() == "approve":
            return "There isn't a calendar change waiting for approval."

        user_turn = build_user_turn(user_text, sender_display_name)
        messages = list(self.history[chat_id]) + [user_turn]
        stable_request_id = request_id or f"telegram:{chat_id}:{user_id}"

        def run_tool(name: str, args: dict):
            if name in CALENDAR_MUTATION_TOOLS:
                return self._propose_actions(
                    actor=actor,
                    actions=[(name, args)],
                    request_id=stable_request_id,
                    request_text=user_text,
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
            return self._propose_actions(
                actor=actor,
                actions=actions,
                request_id=stable_request_id,
                request_text=user_text,
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
        history_text = (
            "A calendar change was proposed but has not run."
            if self.approvals.get(actor) is not None
            else text or "…"
        )
        self._record_history_turn(
            chat_id,
            user_turn,
            {"role": "assistant", "content": history_text},
        )
        return text
