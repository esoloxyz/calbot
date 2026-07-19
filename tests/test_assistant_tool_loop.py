import json
import unittest
from types import SimpleNamespace

from assistant_tool_loop import claims_calendar_success, run_assistant_turn


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


def run_turn(responses, run_tool=lambda name, args: "{}"):
    return run_assistant_turn(
        claude_client=FakeClaude(responses),
        model="test-model",
        system_prompt="test system prompt",
        tools=[],
        messages=[{"role": "user", "content": "wedding Friday at 7"}],
        run_tool=run_tool,
        max_tool_rounds=10,
    )


class CalendarConfirmationGuardTests(unittest.TestCase):
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
            claims_calendar_success("Done! Bari and John's wedding was added for Friday at 7.")
        )

        reply = run_turn([text_response("Done — here's the draft.")])

        self.assertEqual(reply, "Done — here's the draft.")


if __name__ == "__main__":
    unittest.main()
