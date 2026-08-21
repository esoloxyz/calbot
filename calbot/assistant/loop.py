"""Bounded OpenAI Responses tool loop for calendar reads and verified changes."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from calbot.assistant.execution import (
    ToolExecutionResult,
    _tool_outcome,
)
from calbot.assistant.postconditions import claims_calendar_success
from calbot.calendar.contracts import CALENDAR_MUTATION_TOOLS


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
    openai_client,
    model: str,
    system_prompt: str,
    tools: list,
    messages: list,
    run_tool: Callable[[str, dict], str],
    run_tool_batch=None,
    max_tool_rounds: int,
    safety_identifier: str = "",
    logger=None,
) -> str:
    """Run one model turn while keeping calendar writes executor-owned."""
    active_log = logger or log
    input_items = list(messages)
    openai_tools = [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
            # Existing calendar schemas intentionally have optional fields. The
            # executor remains the final validation boundary for every call.
            "strict": False,
        }
        for tool in tools
    ]
    started_at = time.monotonic()
    tool_calls = 0
    result_chars = 0

    for _ in range(max_tool_rounds):
        if time.monotonic() - started_at > MAX_ASSISTANT_TURN_SECONDS:
            return "that request took too long. please try it as a smaller request."

        request = {
            "model": model,
            "max_output_tokens": 2048,
            "instructions": system_prompt,
            "tools": openai_tools,
            "input": input_items,
            "reasoning": {"effort": "low"},
            "text": {"verbosity": "low"},
            "store": False,
        }
        if safety_identifier:
            request["safety_identifier"] = safety_identifier
        response = openai_client.responses.create(
            **request,
        )
        function_calls = [
            item for item in response.output if item.type == "function_call"
        ]
        if not function_calls:
            text = (response.output_text or "").strip()
            if claims_calendar_success(text):
                return (
                    "i didn't change the calendar because no verified calendar "
                    "write ran. please try again."
                )
            return text

        if tool_calls + len(function_calls) > MAX_TOOL_CALLS_PER_TURN:
            return (
                "that request needs too many calendar operations. please split it up."
            )
        tool_calls += len(function_calls)
        input_items.extend(response.output)

        parsed_calls = []
        for call in function_calls:
            try:
                arguments = json.loads(call.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("function arguments must be an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                active_log.warning("tool %s returned invalid arguments", call.name)
                arguments = {}
            parsed_calls.append((call, arguments))

        mutations = [
            (call, arguments)
            for call, arguments in parsed_calls
            if call.name in CALENDAR_MUTATION_TOOLS
        ]
        if len(mutations) > 1 and run_tool_batch is not None:
            execution = _as_execution(
                run_tool_batch(
                    [(call.name, arguments) for call, arguments in mutations]
                )
            )
            return execution.user_reply or (
                "i couldn't verify those calendar changes. please try again."
            )

        function_outputs = []
        for call, arguments in parsed_calls:
            active_log.info("tool %s started", call.name)
            execution = _as_execution(run_tool(call.name, arguments))
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
            active_log.info("tool %s completed %s", call.name, _tool_outcome(output))

            if execution.halt:
                return execution.user_reply or (
                    "i couldn't verify that calendar change. please try again."
                )
            function_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output,
                }
            )

        input_items.extend(function_outputs)

    return "that took too many steps. please rephrase it as a smaller request."
