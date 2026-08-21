import json
import unittest
from types import SimpleNamespace

from calbot.assistant.execution import ToolExecutionResult
from calbot.assistant.loop import run_assistant_turn


def text_response(text):
    return SimpleNamespace(
        output_text=text,
        output=[SimpleNamespace(type="message")],
    )


def tool_response(*calls):
    return SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                arguments=json.dumps(arguments),
                call_id=f"call-{index}",
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ],
    )


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class AssistantToolLoopTests(unittest.TestCase):
    def run_loop(self, responses, run_tool, run_tool_batch=None):
        client = SimpleNamespace(responses=FakeResponses(responses))
        reply = run_assistant_turn(
            openai_client=client,
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
        self.assertIn("Dinner", repr(client.responses.calls[1]["input"]))

    def test_mutation_returns_deterministic_execution_reply(self):
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
                output='{"status":"created"}',
                user_reply="Done — Dinner is on the calendar.",
                halt=True,
            ),
        )

        self.assertEqual(reply, "Done — Dinner is on the calendar.")
        self.assertEqual(len(client.responses.calls), 1)

    def test_multiple_mutations_execute_as_one_batch(self):
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
                output='{"status":"created"}',
                user_reply="Done — Dinner and Brunch are on the calendar.",
                halt=True,
            ),
        )

        self.assertEqual(reply, "Done — Dinner and Brunch are on the calendar.")
        self.assertEqual(len(calls[0]), 2)

    def test_unverified_model_success_is_rejected(self):
        reply, _ = self.run_loop(
            [text_response("I've added dinner to your calendar.")],
            lambda name, args: "",
        )

        self.assertIn("didn't change", reply)

    def test_requests_use_terra_latency_and_privacy_settings(self):
        client = SimpleNamespace(responses=FakeResponses([text_response("hello")]))

        run_assistant_turn(
            openai_client=client,
            model="gpt-5.6-terra",
            system_prompt="calendar only",
            tools=[
                {
                    "name": "list_events",
                    "description": "List calendar events.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"time_min": {"type": "string"}},
                    },
                }
            ],
            messages=[{"role": "user", "content": "what's today?"}],
            run_tool=lambda name, args: "",
            max_tool_rounds=4,
            safety_identifier="hashed-user-id",
        )

        request = client.responses.calls[0]
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["text"], {"verbosity": "low"})
        self.assertFalse(request["store"])
        self.assertEqual(request["safety_identifier"], "hashed-user-id")
        self.assertEqual(request["tools"][0]["type"], "function")
        self.assertEqual(request["tools"][0]["parameters"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
