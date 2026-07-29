import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace

from calbot.runtime import BlockingBridge, BotConfig, BotRuntime


def tool_response(name, arguments, tool_id="tool-1"):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use",
                name=name,
                input=arguments,
                id=tool_id,
            )
        ],
    )


def multi_tool_response(*calls):
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


class FakeCalendar:
    def __init__(self):
        self.calls = []

    def preview_mutation(self, name, args):
        if name == "create_event":
            return {"action": name, "event": dict(args)}
        return {
            "action": name,
            "current_event": {
                "id": args["event_id"],
                "title": "Dinner",
                "start": "2026-07-28T19:00:00-04:00",
                "end": "2026-07-28T21:00:00-04:00",
            },
            "event_etag": "etag-v1",
            "changes": {key: value for key, value in args.items() if key != "event_id"},
        }

    def run_tool(self, name, args):
        self.calls.append((name, dict(args)))
        statuses = {
            "create_event": "created",
            "update_event": "updated",
            "delete_event": "deleted",
        }
        if name == "list_events":
            return json.dumps({"events": []})
        return json.dumps({"status": statuses[name], "id": "event-1"})


def config():
    return BotConfig(
        telegram_token="telegram-token",
        anthropic_api_key="anthropic-key",
        allowed_chat_id=-100123,
        timezone="America/New_York",
        model="test-model",
        bot_owner="Test Couple",
    )


def runtime_with(responses):
    return BotRuntime(
        config=config(),
        claude_client=SimpleNamespace(messages=FakeMessages(responses)),
        calendar_client=FakeCalendar(),
        tools=[],
    )


class BotRuntimeTests(unittest.TestCase):
    def test_create_is_previewed_then_executed_once(self):
        runtime = runtime_with(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-28T19:00:00-04:00",
                        "end": "2026-07-28T21:00:00-04:00",
                    },
                )
            ]
        )

        proposal = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="Dinner tonight at 7",
            request_id="telegram:-100123:42",
        )
        approved = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="approve",
        )
        replay = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="approve",
        )

        self.assertIn("Calendar change awaiting approval", proposal)
        self.assertIn("Dinner", proposal)
        self.assertEqual(approved, "Done — Dinner is on the calendar.")
        self.assertEqual(replay, "There isn't a calendar change waiting for approval.")
        self.assertEqual(len(runtime.cal.calls), 1)
        self.assertEqual(
            runtime.cal.calls[0][1]["_idempotency_key"],
            "telegram:-100123:42:1",
        )

    def test_approval_is_bound_to_requesting_user(self):
        runtime = runtime_with(
            [
                tool_response(
                    "delete_event",
                    {"event_id": "event-1"},
                )
            ]
        )
        runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="Delete dinner",
        )

        other_reply = runtime.ask(
            chat_id=-100123,
            user_id=202,
            user_text="approve",
        )

        self.assertIn("isn't a calendar change", other_reply)
        self.assertIsNotNone(runtime.approvals.get((-100123, 101)))
        self.assertEqual(runtime.cal.calls, [])

    def test_unrelated_message_cancels_pending_change(self):
        runtime = runtime_with(
            [
                tool_response(
                    "create_event",
                    {
                        "title": "Dinner",
                        "start": "2026-07-28T19:00:00-04:00",
                        "end": "2026-07-28T21:00:00-04:00",
                    },
                ),
                SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text="Tomorrow is open.")],
                ),
            ]
        )
        runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="Add dinner",
        )

        reply = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="What about tomorrow?",
        )

        self.assertEqual(reply, "Tomorrow is open.")
        self.assertIsNone(runtime.approvals.get((-100123, 101)))

    def test_batch_uses_one_approval_and_executes_each_action(self):
        runtime = runtime_with(
            [
                multi_tool_response(
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
            ]
        )

        proposal = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="Add dinner and brunch",
        )
        approved = runtime.ask(
            chat_id=-100123,
            user_id=101,
            user_text="approve",
        )

        self.assertIn("2 calendar changes", proposal)
        self.assertEqual(len(runtime.cal.calls), 2)
        self.assertIn("Dinner", approved)
        self.assertIn("Brunch", approved)

    def test_configuration_is_calendar_only(self):
        parsed = BotConfig.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "ANTHROPIC_API_KEY": "key",
                "ALLOWED_CHAT_ID": "-100123",
                "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                "CALENDAR_ID": "shared@example.com",
                "ALLOWED_USER_IDS": "101,202",
            }
        )

        self.assertEqual(parsed.allowed_user_ids, frozenset({101, 202}))
        self.assertFalse(hasattr(parsed, "tempo_bin"))
        self.assertFalse(hasattr(parsed, "bot_mode"))

    def test_system_prompt_is_narrow(self):
        prompt = runtime_with([]).system_prompt()

        self.assertIn("shared Google Calendar", prompt)
        self.assertIn("scope is intentionally narrow", prompt)
        self.assertNotIn("Tempo", prompt)
        self.assertNotIn("DoorDash", prompt)


class BlockingBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_serializes_blocking_calls(self):
        bridge = BlockingBridge()
        active = 0
        maximum = 0
        lock = threading.Lock()

        def work():
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1

        await asyncio.gather(bridge.run(work), bridge.run(work))

        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
