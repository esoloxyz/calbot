"""Claude tool loop with verified calendar action confirmations."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

from assistant_postconditions import (
    CALENDAR_MUTATION_TOOLS,
    _CALENDAR_READ_INTENT,
    _CALENDAR_SUBJECT,
    _EXTERNAL_ACTION_SUBJECT,
    _TASK_STATUS_INTENT,
    _calendar_action_key,
    _clearly_reports_no_action_or_asks,
    _generic_calendar_acknowledgement,
    _latest_user_text,
    _requests_side_effect,
    _successful_object_result,
    _verified_side_effect_success,
    calendar_action_reply,
    claims_calendar_success as claims_calendar_success,
    claims_unverified_side_effect_success,
)
from assistant_tool_execution import (
    ToolExecutionResult as ToolExecutionResult,
    _tool_outcome,
)


log = logging.getLogger("assistant-bot")

MAX_TOOL_CALLS_PER_TURN = 8
MAX_TOOL_RESULT_CHARS_PER_TURN = 64 * 1024
MAX_ASSISTANT_TURN_SECONDS = 150


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
    """Run one Claude turn and enforce calendar side-effect postconditions."""
    active_log = logger or log
    messages = list(messages)
    calendar_replies = {}
    retried_unverified_claim = False
    retried_calendar_residual = False
    text_only_retry = False
    tool_calls = 0
    tool_result_chars = 0
    tool_result_budget_exhausted = False
    calendar_list_incomplete = False
    calendar_list_failed = False
    requested_side_effect = _requests_side_effect(messages)
    initial_user_text = _latest_user_text(messages)
    calendar_read_requested = bool(_CALENDAR_READ_INTENT.search(initial_user_text))
    task_status_requested = bool(_TASK_STATUS_INTENT.search(initial_user_text))
    calendar_list_succeeded = False
    task_status_attempted = False
    task_status_succeeded = False
    task_status_reply = ""
    side_effect_executor_seen = False
    started_at = time.monotonic()

    def with_calendar_completeness_notice(text: str) -> str:
        if calendar_list_failed:
            return (
                "I couldn't check the calendar, so I can't verify the schedule "
                "or any availability claim from this response."
            )
        if not calendar_list_incomplete:
            return text
        notice = (
            "Calendar result limit reached; more events may exist. Ask me to "
            "continue from the next page before treating this as complete."
        )
        return f"{text}\n{notice}" if text else notice

    def boundary_reply_with_residual(
        boundary_reply: str, round_text: str, *, action_domain: str
    ) -> str:
        if not round_text:
            return boundary_reply

        # The current assistant response contains unmatched tool_use blocks.
        # Recovery must use a text-only copy or Anthropic rejects the transcript.
        recovery_messages = list(messages[:-1])
        recovery_messages.append({"role": "assistant", "content": round_text})
        recovery_messages.append(
            {
                "role": "user",
                "content": (
                    "The proposed action has not run and the application will show "
                    "its approval prompt. Return only the non-action answer from your "
                    "preceding response, or exactly PASS if there is none. Do not "
                    "claim any calendar or payment action succeeded."
                ),
            }
        )
        try:
            recovery = claude_client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                tools=[],
                messages=recovery_messages,
            )
        except Exception:
            active_log.warning("Could not recover mixed non-action reply")
            return boundary_reply
        residual = "".join(
            block.text for block in recovery.content if block.type == "text"
        ).strip()
        if (
            not residual
            or residual.casefold() == "pass"
            or claims_unverified_side_effect_success(residual)
            or (
                action_domain == "calendar"
                and _CALENDAR_SUBJECT.search(residual)
                and not _clearly_reports_no_action_or_asks(residual)
            )
            or (
                action_domain == "service"
                and _EXTERNAL_ACTION_SUBJECT.search(residual)
                and not _clearly_reports_no_action_or_asks(residual)
            )
        ):
            return boundary_reply
        return f"{residual}\n{boundary_reply}"

    for _ in range(max_tool_rounds):
        if time.monotonic() - started_at > MAX_ASSISTANT_TURN_SECONDS:
            return with_calendar_completeness_notice(
                "I stopped because this request exceeded the safe time limit."
            )
        response = claude_client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            tools=[] if text_only_retry else tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()

            if calendar_replies:
                action_text = "\n".join(calendar_replies.values())
                if not text or text.casefold() == "pass":
                    return action_text
                if claims_unverified_side_effect_success(text):
                    if retried_calendar_residual:
                        return action_text
                    retried_calendar_residual = True
                    text_only_retry = True
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The application will render verified calendar confirmations. "
                                "Return only the remaining non-calendar answer from the original "
                                "request, or exactly PASS if there is none. Do not claim that a "
                                "calendar action succeeded."
                            ),
                        }
                    )
                    continue
                if _generic_calendar_acknowledgement(text):
                    return action_text
                return f"{action_text}\n{text}"

            if calendar_read_requested and calendar_list_failed:
                return with_calendar_completeness_notice("")
            if task_status_attempted and not task_status_succeeded:
                return (
                    "I couldn't verify the task status, so I won't claim whether "
                    "it is running or complete. Please try again with the exact "
                    "run ID."
                )
            if task_status_succeeded and task_status_reply:
                return task_status_reply

            structurally_unverified = (
                (requested_side_effect and not side_effect_executor_seen)
                or (calendar_read_requested and not calendar_list_succeeded)
                or (task_status_requested and not task_status_succeeded)
            ) and not _clearly_reports_no_action_or_asks(text)
            claimed_success = claims_unverified_side_effect_success(text)
            unverified_claim = claimed_success
            if unverified_claim or structurally_unverified:
                if retried_unverified_claim:
                    return (
                        "I couldn't verify that action, so I didn't claim it "
                        "succeeded. Please try again."
                    )
                retried_unverified_claim = True
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "No side-effect executor succeeded in this turn. Do not "
                            "claim a calendar or payment action is complete. Use the "
                            "appropriate tool now, or clearly say no action was taken."
                        ),
                    }
                )
                continue

            return with_calendar_completeness_notice(text)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        round_text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        calendar_blocks = [
            block
            for block in response.content
            if block.type == "tool_use" and block.name in CALENDAR_MUTATION_TOOLS
        ]
        tool_blocks = [block for block in response.content if block.type == "tool_use"]
        if tool_calls + len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
            return with_calendar_completeness_notice(
                "I stopped before running those tools because the request exceeded "
                "the safe per-turn tool-call limit. Please split it up."
            )
        tool_calls += len(tool_blocks)
        if run_tool_batch is not None and len(calendar_blocks) > 1:
            for block in calendar_blocks:
                active_log.info("tool %s started", block.name)
            execution = run_tool_batch(
                [(block.name, dict(block.input)) for block in calendar_blocks]
            )
            if not isinstance(execution, ToolExecutionResult):
                execution = ToolExecutionResult(output=str(execution))
            for block in calendar_blocks:
                active_log.info(
                    "tool %s completed %s",
                    block.name,
                    _tool_outcome(execution.output),
                )
            boundary_reply = execution.user_reply or (
                "Those actions need explicit confirmation before they can run."
            )
            return boundary_reply_with_residual(
                boundary_reply, round_text, action_domain="calendar"
            )
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_args = dict(block.input)
            active_log.info("tool %s started", block.name)
            if tool_result_budget_exhausted:
                execution = ToolExecutionResult(
                    output=json.dumps(
                        {
                            "error": "Tool-result context budget exhausted",
                            "error_code": "tool_result_budget_exceeded",
                        }
                    )
                )
            else:
                execution = run_tool(block.name, dict(tool_args))
            if isinstance(execution, ToolExecutionResult):
                output = str(execution.output)
            else:
                output = str(execution)
                execution = ToolExecutionResult(output=output)
            remaining_result_chars = MAX_TOOL_RESULT_CHARS_PER_TURN - tool_result_chars
            if len(output) > remaining_result_chars:
                output = json.dumps(
                    {
                        "error": (
                            "Tool result exceeded the safe per-turn context budget; "
                            "its contents were omitted"
                        ),
                        "error_code": "tool_result_budget_exceeded",
                    }
                )
                tool_result_budget_exhausted = True
            else:
                tool_result_chars += len(output)
            active_log.info("tool %s completed %s", block.name, _tool_outcome(output))
            if execution.halt:
                boundary_reply = execution.user_reply or (
                    "That action needs explicit confirmation before it can run."
                )
                return boundary_reply_with_residual(
                    boundary_reply,
                    round_text,
                    action_domain=(
                        "calendar"
                        if block.name in CALENDAR_MUTATION_TOOLS
                        else "service"
                    ),
                )
            if _verified_side_effect_success(block.name, output):
                side_effect_executor_seen = True
            action_reply = calendar_action_reply(block.name, tool_args, output)
            if action_reply:
                key = _calendar_action_key(block.name, tool_args)
                calendar_replies[key] = action_reply
            if block.name == "list_events":
                try:
                    list_payload = json.loads(output)
                    if (
                        not isinstance(list_payload, dict)
                        or list_payload.get("error")
                        or list_payload.get("error_code")
                    ):
                        calendar_list_failed = True
                    else:
                        calendar_list_succeeded = True
                        if list_payload.get("truncated"):
                            calendar_list_incomplete = True
                except (TypeError, json.JSONDecodeError):
                    calendar_list_failed = True
            is_executor_status_poll = block.name == "tempo_task_status" or (
                block.name == "tempo_call_service"
                and str(tool_args.get("method", "POST")).upper() == "GET"
                and not tool_args.get("body")
                and tool_args.get("max_spend", "") in {"", "0"}
            )
            if is_executor_status_poll:
                task_status_attempted = True
                status_payload = _successful_object_result(output)
                status = status_payload.get("status") if status_payload else None
                if isinstance(status, str) and re.fullmatch(
                    r"[A-Za-z0-9_. -]{1,80}", status
                ):
                    task_status_succeeded = True
                    task_status_reply = f"Task status: {status}."
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        messages.append({"role": "user", "content": results})

    return with_calendar_completeness_notice(
        "Sorry — that took too many steps. Try rephrasing?"
    )
