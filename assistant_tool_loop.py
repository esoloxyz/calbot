"""Claude tool loop with verified calendar action confirmations."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable


log = logging.getLogger("assistant-bot")

CALENDAR_MUTATION_TOOLS = {"create_event", "update_event", "delete_event"}
_CALENDAR_SUBJECT = re.compile(
    r"\b(?:cal(?:endar)?|event|appointment|reservation|wedding|dinner|lunch|"
    r"meeting|party|trip|flight|concert|birthday|plans?)\b",
    re.IGNORECASE,
)
_COMPLETED_ACTION = re.compile(
    r"\b(?:added|created|scheduled|booked|updated|changed|deleted|removed|"
    r"cancelled|canceled)\b",
    re.IGNORECASE,
)
_ON_CALENDAR = re.compile(
    r"\b(?:it(?:'s| is)|that(?:'s| is)|now)\s+on\s+(?:the|your|my|our)\s+"
    r"cal(?:endar)?\b|\bon\s+(?:the|your|my|our)\s+cal(?:endar)?\s+now\b",
    re.IGNORECASE,
)


def claims_calendar_success(text: str) -> bool:
    """Return whether text claims that a calendar mutation already succeeded."""
    text = (text or "").strip()
    if not text:
        return False
    if _ON_CALENDAR.search(text):
        return True
    return bool(_CALENDAR_SUBJECT.search(text) and _COMPLETED_ACTION.search(text))


def _calendar_action_key(name: str, args: dict) -> tuple:
    if name == "create_event":
        return name, args.get("title", ""), args.get("start", "")
    return name, args.get("event_id", "")


def _calendar_action_reply(name: str, args: dict, output: str) -> str | None:
    if name not in CALENDAR_MUTATION_TOOLS:
        return None

    title = args.get("title") or "the event"
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        result = {"error": "the calendar returned an unreadable response"}

    error = result.get("error")
    if error:
        verbs = {
            "create_event": "add",
            "update_event": "update",
            "delete_event": "delete",
        }
        if name == "create_event":
            return f"I couldn't {verbs[name]} {title} to the calendar: {error}"
        return f"I couldn't {verbs[name]} {title}: {error}"

    status = result.get("status")
    if name == "create_event" and status == "duplicate":
        existing_title = result.get("title") or title
        return f"That's already on the calendar: {existing_title}."
    if name == "create_event" and status == "created":
        return f"Done — {title} is on the calendar."
    if name == "update_event" and status == "updated":
        return f"Done — {title} was updated."
    if name == "delete_event" and status == "deleted":
        return "Done — the calendar event was deleted."

    return (
        f"I couldn't verify the calendar result for {title}, so I didn't claim "
        "it succeeded."
    )


def run_assistant_turn(
    *,
    claude_client,
    model: str,
    system_prompt: str,
    tools: list,
    messages: list,
    run_tool: Callable[[str, dict], str],
    max_tool_rounds: int,
    logger=None,
) -> str:
    """Run one Claude turn and enforce calendar side-effect postconditions."""
    active_log = logger or log
    messages = list(messages)
    calendar_replies = {}
    retried_unverified_claim = False

    for _ in range(max_tool_rounds):
        response = claude_client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()

            if calendar_replies:
                return "\n".join(calendar_replies.values())

            if claims_calendar_success(text):
                if retried_unverified_claim:
                    return (
                        "I couldn't verify that calendar action, so I didn't claim it "
                        "succeeded. Please try again."
                    )
                retried_unverified_claim = True
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "No calendar mutation tool succeeded in this turn. Do not "
                            "claim the action is complete. Use the appropriate calendar "
                            "tool now, or clearly say that no action was taken."
                        ),
                    }
                )
                continue

            return text

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_args = dict(block.input)
            active_log.info("tool %s %s", block.name, tool_args)
            output = run_tool(block.name, tool_args)
            active_log.info("tool %s result: %s", block.name, output[:200])
            action_reply = _calendar_action_reply(block.name, tool_args, output)
            if action_reply:
                key = _calendar_action_key(block.name, tool_args)
                calendar_replies[key] = action_reply
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        messages.append({"role": "user", "content": results})

    return "Sorry — that took too many steps. Try rephrasing?"
