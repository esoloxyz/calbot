import json
import unittest
from types import SimpleNamespace

from calbot.assistant.execution import ToolExecutionResult
from calbot.assistant.loop import run_assistant_turn


def text_response(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )


def tool_response(*calls):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name=name,
                input=arguments,
                id=f"tool-{index}",
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ],
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class AssistantToolLoopTests(unittest.TestCase):
    def run_loop(self, responses, run_tool, run_tool_batch=None):
        client = SimpleNamespace(messages=FakeMessages(responses))
        reply = run_assistant_turn(
            claude_client=client,
            model="test",
            system_prompt="calendar only",
            tools=[],
            messages=[{"role": "user", "content": "test"}],
            run_tool=run_tool,
            run_tool_batch=run_tool_batch,
            max_tool_rounds=4,
        )
        return reply, client

    def test_calendar_read_result_returns_to_model(self):
        reply, client = self.run_loop(
            [
                tool_response(
                    (
                        "list_events",
                        {
                            "time_min": "2026-07-28T00:00:00-04:00",
                            "time_max": "2026-07-29T00:00:00-04:00",
                        },
                    )
                ),
                text_response("You have dinner at 7."),
            ],
            lambda name, args: json.dumps({"events": [{"title": "Dinner"}]}),
        )

        self.assertEqual(reply, "You have dinner at 7.")
        self.assertIn("Dinner", repr(client.messages.calls[1]["messages"]))

    def test_mutation_halts_at_application_approval(self):
        reply, client = self.run_loop(
            [
                tool_response(
                    (
                        "create_event",
                        {
                            "title": "Dinner",
                            "start": "2026-07-28T19:00:00-04:00",
                            "end": "2026-07-28T21:00:00-04:00",
                        },
                    )
                )
            ],
            lambda name, args: ToolExecutionResult(
                output='{"status":"confirmation_required"}',
                user_reply="Reply approve to continue.",
                halt=True,
            ),
        )

        self.assertEqual(reply, "Reply approve to continue.")
        self.assertEqual(len(client.messages.calls), 1)

    def test_multiple_mutations_use_one_batch_proposal(self):
        calls = []

        reply, _ = self.run_loop(
            [
                tool_response(
                    (
                        "create_event",
                        {
                            "title": "Dinner",
                            "start": "2026-07-28T19:00:00-04:00",
                            "end": "2026-07-28T21:00:00-04:00",
                        },
                    ),
                    (
                        "create_event",
                        {
                            "title": "Brunch",
                            "start": "2026-07-29T11:00:00-04:00",
                            "end": "2026-07-29T12:00:00-04:00",
                        },
                    ),
                )
            ],
            lambda name, args: "",
            lambda actions: calls.append(actions)
            or ToolExecutionResult(
                output='{"status":"confirmation_required"}',
                user_reply="2 changes need approval.",
                halt=True,
            ),
        )

        self.assertEqual(reply, "2 changes need approval.")
        self.assertEqual(len(calls[0]), 2)

    def test_unverified_model_success_is_rejected(self):
        reply, _ = self.run_loop(
            [text_response("I've added dinner to your calendar.")],
            lambda name, args: "",
        )

        self.assertIn("didn't change", reply)


if __name__ == "__main__":
    unittest.main()
