import json
import logging
import unittest
from types import SimpleNamespace

from calbot.assistant.loop import (
    ToolExecutionResult,
    claims_calendar_success,
    claims_unverified_side_effect_success,
    run_assistant_turn,
)


def text_response(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )


def tool_response(name, tool_input, tool_id="tool-1"):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name=name,
                input=tool_input,
                id=tool_id,
            )
        ],
    )


def text_and_tool_response(text, name, tool_input, tool_id="tool-1"):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text=text),
            SimpleNamespace(
                type="tool_use",
                name=name,
                input=tool_input,
                id=tool_id,
            ),
        ],
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClaude:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def run_turn(
    responses,
    run_tool=lambda name, args: "{}",
    logger=None,
    user_text="wedding Friday at 7",
):
    return run_assistant_turn(
        claude_client=FakeClaude(responses),
        model="test-model",
        system_prompt="test system prompt",
        tools=[],
        messages=[{"role": "user", "content": user_text}],
        run_tool=run_tool,
        max_tool_rounds=10,
        logger=logger,
    )


class CalendarConfirmationGuardTests(unittest.TestCase):
    def test_per_turn_tool_call_limit_blocks_entire_oversized_tool_batch(self):
        response = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="list_events",
                    input={"time_min": "a", "time_max": "b"},
                    id=f"tool-{index}",
                )
                for index in range(9)
            ],
        )
        calls = []

        reply = run_turn([response], lambda name, args: calls.append((name, args)))

        self.assertEqual(calls, [])
        self.assertIn("tool-call limit", reply)

    def test_large_tool_result_is_omitted_from_model_context(self):
        secret = "PRIVATE-RESULT-" + "X" * (70 * 1024)
        client = FakeClaude(
            [
                tool_response(
                    "tempo_discover_services",
                    {"query": "search"},
                ),
                text_response("The result was too large to inspect safely."),
            ]
        )

        reply = run_assistant_turn(
            claude_client=client,
            model="test-model",
            system_prompt="test",
            tools=[],
            messages=[{"role": "user", "content": "search"}],
            run_tool=lambda name, args: secret,
            max_tool_rounds=10,
        )

        next_context = repr(client.messages.calls[1]["messages"])
        self.assertNotIn("PRIVATE-RESULT", next_context)
        self.assertIn("tool_result_budget_exceeded", next_context)
        self.assertIn("too large", reply)

    def test_truncated_calendar_read_gets_executor_owned_disclosure(self):
        reply = run_turn(
            [
                tool_response(
                    "list_events",
                    {"time_min": "a", "time_max": "b"},
                ),
                text_response("Here are your events."),
            ],
            lambda name, args: json.dumps(
                {
                    "events": [],
                    "truncated": True,
                    "next_page_token": "next",
                }
            ),
            user_text="what is on my calendar?",
        )

        self.assertIn("more events may exist", reply)
        self.assertIn("next page", reply)

    def test_calendar_read_error_discards_unverified_availability_claim(self):
        claim = "You're completely free today — nothing is scheduled."
        reply = run_turn(
            [
                tool_response(
                    "list_events",
                    {"time_min": "a", "time_max": "b"},
                ),
                text_response(claim),
            ],
            lambda name, args: json.dumps({"error": "permission denied"}),
            user_text="am I free today?",
        )

        self.assertNotIn(claim, reply)
        self.assertIn("couldn't check the calendar", reply)
        self.assertIn("can't verify", reply)

    def test_schedule_claims_fail_closed_when_list_events_never_ran(self):
        cases = (
            ("Am I free today?", "You are completely free today."),
            ("What's on my calendar tomorrow?", "Nothing is scheduled tomorrow."),
            ("Do I have plans Friday?", "No, you have no plans Friday."),
        )
        for request, claim in cases:
            with self.subTest(request=request):
                reply = run_turn(
                    [text_response(claim), text_response(claim)],
                    user_text=request,
                )

                self.assertNotIn(claim, reply)
                self.assertIn("couldn't verify", reply)

    def test_calendar_incompleteness_is_monotonic_across_multiple_reads(self):
        outputs = iter(
            [
                json.dumps({"events": [], "truncated": True}),
                json.dumps({"events": [], "truncated": False}),
            ]
        )
        response = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="list_events",
                    input={"time_min": "a", "time_max": "b"},
                    id="tool-1",
                ),
                SimpleNamespace(
                    type="tool_use",
                    name="list_events",
                    input={"time_min": "c", "time_max": "d"},
                    id="tool-2",
                ),
            ],
        )

        reply = run_turn(
            [response, text_response("Here are the events.")],
            lambda name, args: next(outputs),
            user_text="what is on my calendar?",
        )

        self.assertIn("more events may exist", reply)

    def test_false_success_claim_retries_and_requires_a_real_tool_result(self):
        calls = []

        def create(name, args):
            calls.append((name, args))
            return json.dumps({"status": "created", "id": "event-1"})

        reply = run_turn(
            [
                text_response("Done! Wedding added — Friday at 7."),
                tool_response(
                    "create_event",
                    {
                        "title": "Wedding",
                        "start": "2026-11-20T19:00:00-05:00",
                        "end": "2026-11-20T23:00:00-05:00",
                    },
                ),
                text_response("Done!"),
            ],
            create,
        )

        self.assertEqual(reply, "Done — Wedding is on the calendar.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "create_event")

    def test_repeated_false_success_claim_is_not_shown_to_the_user(self):
        reply = run_turn(
            [
                text_response("Done! Wedding added — Friday at 7."),
                text_response("It's on the calendar now!"),
            ]
        )

        self.assertIn("couldn't verify", reply)
        self.assertNotIn("Done", reply)

    def test_successful_create_uses_a_deterministic_app_confirmation(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response("I think that probably worked."),
            ],
            lambda name, args: json.dumps({"status": "created", "id": "event-2"}),
        )

        self.assertEqual(reply, "Done — Dinner is on the calendar.")

    def test_duplicate_create_tells_the_user_about_the_existing_event(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response("Done!"),
            ],
            lambda name, args: json.dumps(
                {
                    "status": "duplicate",
                    "id": "existing-event",
                    "title": "Dinner",
                    "start": "2026-07-19T19:00:00-04:00",
                }
            ),
        )

        self.assertEqual(
            reply,
            "That's already on the calendar: Dinner.",
        )

    def test_calendar_api_error_cannot_be_turned_into_a_success_claim(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response("Done — added!"),
            ],
            lambda name, args: json.dumps({"error": "permission denied"}),
        )

        self.assertEqual(
            reply,
            "I couldn't add Dinner to the calendar: permission denied",
        )

    def test_non_calendar_done_message_is_unchanged(self):
        self.assertFalse(claims_calendar_success("Done — here's the draft."))
        self.assertTrue(
            claims_calendar_success(
                "Done! Bari and John's wedding was added for Friday at 7."
            )
        )

        reply = run_turn(
            [text_response("Done — here's the draft.")],
            user_text="draft a short note",
        )

        self.assertEqual(reply, "Done — here's the draft.")

    def test_false_success_variants_are_recognized_without_broad_matches(self):
        false_successes = (
            "Calendar action complete.",
            "All set!",
            "Good to go.",
            "I've put the wedding in for Friday.",
            "I put it in your calendar.",
            "I've put the dentist visit in your calendar.",
            "I've entered dinner in your calendar.",
            "Your calendar now includes dinner.",
        )
        for claim in false_successes:
            with self.subTest(claim=claim):
                self.assertTrue(claims_calendar_success(claim))

        non_claims = (
            "All set — here's the draft you requested.",
            "The deployment is good to go after its tests pass.",
            "For dinner, put the pan in the oven.",
            "I put the dinner recipe in the shared document.",
            "The dinner set includes plates and napkins.",
        )
        for text in non_claims:
            with self.subTest(text=text):
                self.assertFalse(claims_calendar_success(text))

    def test_false_success_variants_retry_then_fail_closed_without_a_tool(self):
        claims = (
            "Calendar action complete.",
            "All set!",
            "Good to go.",
            "I've put the wedding in for Friday.",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                reply = run_turn([text_response(claim), text_response(claim)])

                self.assertIn("couldn't verify", reply)
                self.assertNotIn(claim, reply)

    def test_false_payment_success_claim_retries_then_fails_closed(self):
        claim = "The $50 payment went through successfully."

        self.assertTrue(claims_unverified_side_effect_success(claim))
        reply = run_turn([text_response(claim), text_response(claim)])

        self.assertIn("couldn't verify", reply)
        self.assertNotIn(claim, reply)
        self.assertIn("verify that action", reply)

    def test_additional_calendar_removal_and_payment_claims_fail_closed(self):
        claims = (
            "I've cleared the meeting from your calendar.",
            "The appointment has been taken off your calendar.",
            "Your invoice has been paid.",
            "Payment complete.",
            "The bill is paid.",
        )

        for claim in claims:
            with self.subTest(claim=claim):
                self.assertTrue(claims_unverified_side_effect_success(claim))
                reply = run_turn([text_response(claim), text_response(claim)])
                self.assertNotIn(claim, reply)
                self.assertIn("verify that action", reply)

    def test_action_intent_fails_closed_for_ordinary_calendar_claims(self):
        cases = (
            ("move dinner to 8 PM", "I've moved dinner to 8 PM."),
            ("reschedule the appointment", "I've rescheduled the appointment."),
            ("remove the meeting", "The meeting is off your calendar."),
            ("push dinner back an hour", "I've pushed dinner back an hour."),
            (
                "pencil dinner into my calendar",
                "I've penciled dinner into your calendar.",
            ),
            (
                "slot the meeting into my calendar",
                "I've slotted the meeting into your calendar.",
            ),
        )
        for request, claim in cases:
            with self.subTest(claim=claim):
                reply = run_turn(
                    [text_response(claim), text_response(claim)],
                    user_text=request,
                )

                self.assertNotIn(claim, reply)
                self.assertIn("couldn't verify", reply)

    def test_failed_service_executor_does_not_authorize_a_success_claim(self):
        claim = "Your artwork has arrived."
        reply = run_turn(
            [
                tool_response(
                    "tempo_call_service",
                    {"url": "https://service.example/image"},
                ),
                text_response(claim),
                text_response(claim),
            ],
            lambda name, args: json.dumps({"error": "URL not discovered"}),
            user_text="generate an image of a cat",
        )

        self.assertNotIn(claim, reply)
        self.assertIn("couldn't verify", reply)

    def test_task_completion_claims_require_task_status_executor(self):
        cases = (
            ("Is the research job done?", "The research job is finished."),
            (
                "Did the service request finish?",
                "The service request completed successfully.",
            ),
            ("check task abc", "Task abc finished successfully."),
        )
        for request, claim in cases:
            with self.subTest(request=request):
                reply = run_turn(
                    [text_response(claim), text_response(claim)],
                    user_text=request,
                )

                self.assertNotIn(claim, reply)
                self.assertIn("couldn't verify", reply)

    def test_task_status_executor_owns_completion_wording(self):
        claim = "The research job is finished."
        reply = run_turn(
            [
                tool_response("tempo_task_status", {"run_id": "task_abc"}),
                text_response(claim),
            ],
            lambda name, args: json.dumps({"status": "running"}),
            user_text="Is the research job done?",
        )

        self.assertEqual(reply, "Task status: running.")

    def test_failed_task_status_cannot_be_rewritten_as_running(self):
        reply = run_turn(
            [
                tool_response("tempo_task_status", {"run_id": "task_123"}),
                text_response("Still running."),
            ],
            lambda name, args: json.dumps({"error": "provider unavailable"}),
            user_text="is it done?",
        )

        self.assertNotIn("Still running", reply)
        self.assertIn("couldn't verify the task status", reply)

    def test_external_service_success_claims_fail_closed_without_executor(self):
        cases = (
            ("generate an image of a puppy", "Your image is ready."),
            ("generate an image of a puppy", "I generated the image for you."),
        )
        for request, claim in cases:
            with self.subTest(claim=claim):
                reply = run_turn(
                    [text_response(claim), text_response(claim)],
                    user_text=request,
                )

                self.assertNotIn(claim, reply)
                self.assertIn("couldn't verify", reply)

    def test_calendar_confirmation_does_not_discard_a_non_calendar_answer(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Umbrella reminder",
                        "start": "2026-07-19T18:00:00-04:00",
                        "end": "2026-07-19T18:15:00-04:00",
                    },
                ),
                text_response("The forecast calls for rain around 6 PM."),
            ],
            lambda name, args: json.dumps({"status": "created", "id": "event-weather"}),
        )

        self.assertIn("The forecast calls for rain around 6 PM.", reply)
        self.assertIn("Umbrella reminder is on the calendar", reply)

    def test_calendar_success_claim_is_removed_while_other_answer_survives(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response("Dinner was added. Rain is likely tomorrow."),
                text_response("Rain is likely tomorrow."),
            ],
            lambda name, args: json.dumps({"status": "created", "id": "event-dinner"}),
        )

        self.assertNotIn("Dinner was added", reply)
        self.assertIn("Done — Dinner is on the calendar.", reply)
        self.assertIn("Rain is likely tomorrow.", reply)

    def test_repeated_calendar_claim_in_residual_falls_back_to_verified_reply(self):
        reply = run_turn(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response("Dinner was added."),
                text_response("Dinner is now on your calendar."),
            ],
            lambda name, args: json.dumps({"status": "created", "id": "event-dinner"}),
        )

        self.assertEqual(reply, "Done — Dinner is on the calendar.")

    def test_tool_logs_do_not_include_arguments_or_result_payloads(self):
        logger = logging.getLogger("calbot-test-tool-redaction")
        with self.assertLogs(logger, level="INFO") as captured:
            run_turn(
                [
                    tool_response(
                        "tempo_call_service",
                        {
                            "url": "https://service.example/private",
                            "body": '{"secret":"sensitive prompt"}',
                        },
                    ),
                    text_response("Finished."),
                ],
                lambda name, args: json.dumps(
                    {
                        "status": "completed",
                        "result": "sensitive output\nFORGED log line",
                    }
                ),
                logger=logger,
            )

        output = "\n".join(captured.output)
        self.assertIn("tempo_call_service", output)
        self.assertNotIn("sensitive prompt", output)
        self.assertNotIn("sensitive output", output)
        self.assertNotIn("FORGED log line", output)
        self.assertNotIn("service.example", output)

    def test_side_effect_proposal_halts_before_the_model_can_switch_actions(self):
        client = FakeClaude(
            [
                tool_response("delete_event", {"event_id": "victim-event"}),
                tool_response(
                    "tempo_call_service",
                    {"url": "https://service.example/alternate"},
                    tool_id="tool-2",
                ),
            ]
        )

        reply = run_assistant_turn(
            claude_client=client,
            model="test-model",
            system_prompt="test",
            tools=[],
            messages=[{"role": "user", "content": "look up the weather"}],
            run_tool=lambda name, args: ToolExecutionResult(
                output='{"error_code":"confirmation_required"}',
                user_reply="Reply approve",
                halt=True,
            ),
            max_tool_rounds=10,
        )

        self.assertEqual(reply, "Reply approve")
        self.assertEqual(len(client.messages.calls), 1)

    def test_non_calendar_text_survives_a_side_effect_approval_boundary(self):
        reply = run_turn(
            [
                text_and_tool_response(
                    "The forecast calls for rain around 6 PM.",
                    "create_event",
                    {
                        "title": "Umbrella reminder",
                        "start": "2026-07-19T18:00:00-04:00",
                        "end": "2026-07-19T18:15:00-04:00",
                    },
                ),
                text_response("The forecast calls for rain around 6 PM."),
            ],
            lambda name, args: ToolExecutionResult(
                output='{"error_code":"confirmation_required"}',
                user_reply="Reply approve",
                halt=True,
            ),
        )

        self.assertIn("forecast calls for rain", reply)
        self.assertIn("Reply approve", reply)

    def test_contextual_payment_explanation_survives_an_approval_boundary(self):
        explanation = "Payment approved means the issuer accepted it."
        reply = run_turn(
            [
                text_and_tool_response(
                    explanation,
                    "create_event",
                    {
                        "title": "Lunch",
                        "start": "2026-07-19T12:00:00-04:00",
                        "end": "2026-07-19T13:00:00-04:00",
                    },
                ),
                text_response(explanation),
            ],
            lambda name, args: ToolExecutionResult(
                output='{"error_code":"confirmation_required"}',
                user_reply="Reply approve",
                halt=True,
            ),
        )

        self.assertIn(explanation, reply)
        self.assertIn("Reply approve", reply)

    def test_false_action_claim_is_removed_without_losing_mixed_answer(self):
        client = FakeClaude(
            [
                text_and_tool_response(
                    "Rain is likely at 6 PM; I added the umbrella reminder.",
                    "create_event",
                    {
                        "title": "Umbrella reminder",
                        "start": "2026-07-19T18:00:00-04:00",
                        "end": "2026-07-19T18:15:00-04:00",
                    },
                ),
                text_response("Rain is likely at 6 PM."),
            ]
        )
        reply = run_assistant_turn(
            claude_client=client,
            model="test-model",
            system_prompt="test",
            tools=[],
            messages=[{"role": "user", "content": "weather plus reminder"}],
            run_tool=lambda name, args: ToolExecutionResult(
                output='{"error_code":"confirmation_required"}',
                user_reply="Reply approve",
                halt=True,
            ),
            max_tool_rounds=10,
        )

        self.assertIn("Rain is likely at 6 PM.", reply)
        self.assertIn("Reply approve", reply)
        self.assertNotIn("I added", reply)
        recovery_messages = client.messages.calls[1]["messages"]
        for message in recovery_messages:
            content = message["content"]
            if isinstance(content, list):
                self.assertFalse(
                    any(getattr(block, "type", "") == "tool_use" for block in content)
                )

    def test_calendar_claim_is_discarded_at_preapproval_recovery_boundary(self):
        claim = "I've penciled dinner into your calendar."
        reply = run_turn(
            [
                text_and_tool_response(
                    claim,
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-19T19:00:00-04:00",
                        "end": "2026-07-19T21:00:00-04:00",
                    },
                ),
                text_response(claim),
            ],
            lambda name, args: ToolExecutionResult(
                output='{"error_code":"confirmation_required"}',
                user_reply="Reply approve",
                halt=True,
            ),
            user_text="pencil dinner into my calendar",
        )

        self.assertEqual(reply, "Reply approve")

    def test_preapproval_completion_phrases_are_never_shown(self):
        cases = (
            ("The payment is complete.", "tempo_call_service"),
            ("All set — the event is ready.", "create_event"),
        )
        for claim, tool_name in cases:
            with self.subTest(claim=claim):
                reply = run_turn(
                    [
                        text_and_tool_response(
                            claim,
                            tool_name,
                            {"url": "https://service.example"}
                            if tool_name == "tempo_call_service"
                            else {
                                "title": "Dinner",
                                "start": "2026-07-19T19:00:00-04:00",
                                "end": "2026-07-19T21:00:00-04:00",
                            },
                        ),
                        text_response(claim),
                    ],
                    lambda name, args: ToolExecutionResult(
                        output='{"error_code":"confirmation_required"}',
                        user_reply="Reply approve",
                        halt=True,
                    ),
                )

                self.assertEqual(reply, "Reply approve")


if __name__ == "__main__":
    unittest.main()
