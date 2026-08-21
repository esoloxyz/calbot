import json
import unittest
from types import SimpleNamespace

from calbot.assistant.planner import (
    PLAN_TOOL,
    PlanningError,
    TurnMode,
    plan_assistant_turn,
)


def planner_response(arguments):
    return SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name="plan_turn",
                arguments=json.dumps(arguments),
                call_id="plan-1",
            )
        ],
    )


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class SemanticPlannerTests(unittest.TestCase):
    def test_forced_strict_plan_is_parsed(self):
        responses = FakeResponses(
            planner_response(
                {
                    "mode": "calendar_write",
                    "calendar_request": "Move dinner to 8 PM Friday.",
                    "reply": None,
                    "clarification_question": None,
                }
            )
        )
        plan = plan_assistant_turn(
            openai_client=SimpleNamespace(responses=responses),
            model="gpt-5.6-terra",
            messages=[{"role": "user", "content": "actually 8"}],
            actor_name="Ezra",
            personality="friendly",
            safety_identifier="safe-user",
        )

        self.assertIs(plan.mode, TurnMode.CALENDAR_WRITE)
        self.assertEqual(plan.calendar_request, "Move dinner to 8 PM Friday.")
        request = responses.calls[0]
        self.assertTrue(PLAN_TOOL["strict"])
        self.assertEqual(
            request["tool_choice"], {"type": "function", "name": "plan_turn"}
        )
        self.assertFalse(request["parallel_tool_calls"])
        self.assertFalse(request["store"])
        self.assertEqual(request["safety_identifier"], "safe-user")

    def test_conversation_requires_a_reply(self):
        responses = FakeResponses(
            planner_response(
                {
                    "mode": "conversation",
                    "calendar_request": None,
                    "reply": None,
                    "clarification_question": None,
                }
            )
        )

        with self.assertRaises(PlanningError):
            plan_assistant_turn(
                openai_client=SimpleNamespace(responses=responses),
                model="test",
                messages=[{"role": "user", "content": "hello"}],
                actor_name="calendar owner",
                personality="friendly",
            )

    def test_only_calendar_write_may_ask_for_clarification(self):
        responses = FakeResponses(
            planner_response(
                {
                    "mode": "calendar_read",
                    "calendar_request": "Find dinner.",
                    "reply": None,
                    "clarification_question": "Which dinner?",
                }
            )
        )

        with self.assertRaises(PlanningError):
            plan_assistant_turn(
                openai_client=SimpleNamespace(responses=responses),
                model="test",
                messages=[{"role": "user", "content": "where is dinner?"}],
                actor_name="Sarah",
                personality="friendly",
            )


if __name__ == "__main__":
    unittest.main()
