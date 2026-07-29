"""Bounded Claude tool loop for calendar reads and change proposals."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from calbot.assistant.execution import (
    ToolExecutionResult as ToolExecutionResult,
    _tool_outcome,
)
from calbot.assistant.postconditions import (
    CALENDAR_MUTATION_TOOLS,
    calendar_action_reply as calendar_action_reply,
    claims_calendar_success as claims_calendar_success,
)


log = logging.getLogger("assistant-bot")
MAX_TOOL_CALLS_PER_TURN = 8
MAX_TOOL_RESULT_CHARS_PER_TURN = 64 * 1024
MAX_ASSISTANT_TURN_SECONDS = 120


def _as_execution(value) -> ToolExecutionResult:
    if isinstance(value, ToolExecutionResult):
        return value
    return ToolExecutionResult(output=str(value))


def run_assistant_turn(
    *,
    claude_client,
    model: str,
    system_prompt: str,
    tools: list,
    messages: list,
    run_tool: Callable[[str, dict], str],
    run_tool_batch=None,
    max_tool_rounds: int,
    logger=None,
) -> str:
    """Run one model turn while keeping all calendar writes behind approval."""
    active_log = logger or log
    transcript = list(messages)
    started_at = time.monotonic()
    tool_calls = 0
    result_chars = 0

    for _ in range(max_tool_rounds):
        if time.monotonic() - started_at > MAX_ASSISTANT_TURN_SECONDS:
            return "That request took too long. Please try it as a smaller request."

        response = claude_client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            tools=tools,
            messages=transcript,
        )
        tool_blocks = [block for block in response.content if block.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_blocks:
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
            if claims_calendar_success(text):
                return (
                    "I didn't change the calendar because no verified calendar "
                    "write ran. Please try again."
                )
            return text

        if tool_calls + len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
            return (
                "That request needs too many calendar operations. Please split it up."
            )
        tool_calls += len(tool_blocks)
        transcript.append({"role": "assistant", "content": response.content})

        mutations = [
            block for block in tool_blocks if block.name in CALENDAR_MUTATION_TOOLS
        ]
        if len(mutations) > 1 and run_tool_batch is not None:
            execution = _as_execution(
                run_tool_batch([(block.name, dict(block.input)) for block in mutations])
            )
            return execution.user_reply or (
                "Those calendar changes need approval before they can run."
            )

        tool_results = []
        for block in tool_blocks:
            active_log.info("tool %s started", block.name)
            execution = _as_execution(run_tool(block.name, dict(block.input)))
            output = str(execution.output)
            remaining = MAX_TOOL_RESULT_CHARS_PER_TURN - result_chars
            if len(output) > remaining:
                output = json.dumps(
                    {
                        "error": "Calendar result exceeded the safe context limit",
                        "error_code": "tool_result_budget_exceeded",
                    }
                )
            else:
                result_chars += len(output)
            active_log.info("tool %s completed %s", block.name, _tool_outcome(output))

            if execution.halt:
                return execution.user_reply or (
                    "That calendar change needs approval before it can run."
                )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )

        transcript.append({"role": "user", "content": tool_results})

    return "That took too many steps. Please rephrase it as a smaller request."
