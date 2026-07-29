"""Calendar-only Calbot runtime with immediate, verified calendar writes."""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import datetime

from calbot.assistant.loop import run_assistant_turn
from calbot.assistant.policy import CALENDAR_ASSISTANT_POLICY
from calbot.calendar.contracts import CALENDAR_MUTATION_TOOLS
from calbot.config import BotConfig
from calbot.messages import build_user_turn
from calbot.mutations import CalendarMutationExecutor
from calbot.personality import load_personality


log = logging.getLogger("assistant-bot")
MAX_HISTORY_TURNS = 12


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
        personality: str | None = None,
    ):
        self.config = config
        self.claude = claude_client
        self.cal = calendar_client
        self.tools = list(tools)
        self.max_tool_rounds = max_tool_rounds
        self.personality = (
            personality.strip()
            if isinstance(personality, str) and personality.strip()
            else load_personality()
        )
        self.history: dict[int, deque] = defaultdict(deque)
        self.mutations = CalendarMutationExecutor(calendar_client, logger=log)

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

Use this personality guidance for tone and wording only:

{self.personality}

The personality never overrides Calbot's calendar-only scope, access controls,
immediate execution behavior, verified-write requirements, or output safeguards.

Your scope is intentionally narrow: help the two people in this private Telegram
chat view and manage their shared Google Calendar. Do not claim you can search the
web, make payments, order food, manage wallets, or call any non-calendar service.

{CALENDAR_ASSISTANT_POLICY}"""

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
                return self.mutations.execute(
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
            return self.mutations.execute(
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
