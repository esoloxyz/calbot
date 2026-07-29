import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from calbot.runtime import BotConfig
from calbot.telegram_app import (
    _authorized,
    _reply_in_chunks,
    _run_digest_command,
    _weekend_window,
    cmd_start,
    on_message,
    telegram_chunks,
)


def config(**overrides):
    values = {
        "telegram_token": "telegram-token",
        "anthropic_api_key": "anthropic-key",
        "allowed_chat_id": -100123,
        "allowed_user_ids": frozenset({101, 202}),
        "bot_owner": "Test Couple",
    }
    values.update(overrides)
    return BotConfig(**values)


def update_for(user_id=101, chat_id=-100123, sender_chat=None):
    message = SimpleNamespace(
        text="hello",
        chat_id=chat_id,
        message_id=8,
        sender_chat=sender_chat,
        reply_to_message=None,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id, first_name="Ezra"),
        message=message,
    )


class TelegramBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_chunks_preserve_text(self):
        text = "x" * 4500
        chunks = telegram_chunks(text)

        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))

    def test_authorization_requires_chat_and_user(self):
        self.assertTrue(_authorized(update_for(), config()))
        self.assertFalse(_authorized(update_for(user_id=303), config()))
        self.assertFalse(_authorized(update_for(chat_id=-999), config()))
        self.assertFalse(_authorized(update_for(sender_chat=object()), config()))

    async def test_chunk_sender_suppresses_internal_data(self):
        message = SimpleNamespace(reply_text=AsyncMock())

        await _reply_in_chunks(
            message,
            '{"action":"create_event","event":{"title":"Dinner"}}',
        )

        sent = message.reply_text.await_args.args[0]
        self.assertIn("clear calendar answer", sent)
        self.assertNotIn("{", sent)

    async def test_unmentioned_message_is_ignored_when_configured(self):
        update = update_for()
        context = SimpleNamespace(
            bot=SimpleNamespace(
                username="calbot",
                id=999,
                send_chat_action=AsyncMock(),
            )
        )
        with (
            patch(
                "calbot.telegram_app._components",
                return_value=(object(), config(respond_to_all=False), object()),
            ),
            patch("calbot.telegram_app._ask", AsyncMock()) as ask,
        ):
            await on_message(update, context)

        ask.assert_not_awaited()

    async def test_start_message_is_calendar_only(self):
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101),
            message=message,
        )
        with patch(
            "calbot.telegram_app._components",
            return_value=(object(), config(), object()),
        ):
            await cmd_start(update, SimpleNamespace())

        reply = message.reply_text.await_args.args[0]
        self.assertIn("shared calendar", reply)
        self.assertIn("/today", reply)
        self.assertNotIn("Tempo", reply)
        self.assertNotIn("DoorDash", reply)


class CalendarWindowTests(unittest.TestCase):
    def test_current_weekend_is_used_friday_through_sunday(self):
        timezone = ZoneInfo("America/New_York")
        expected_start = datetime(2026, 7, 17, tzinfo=timezone)
        expected_end = datetime(2026, 7, 20, tzinfo=timezone)

        for day in (17, 18, 19):
            with self.subTest(day=day):
                start, end = _weekend_window(
                    datetime(2026, 7, day, 15, 30, tzinfo=timezone)
                )
                self.assertEqual(start, expected_start)
                self.assertEqual(end, expected_end)

    def test_coming_friday_is_used_during_workweek(self):
        timezone = ZoneInfo("America/New_York")

        start, end = _weekend_window(datetime(2026, 7, 13, 15, 30, tzinfo=timezone))

        self.assertEqual(start, datetime(2026, 7, 17, tzinfo=timezone))
        self.assertEqual(end, datetime(2026, 7, 20, tzinfo=timezone))


class DigestCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_digest_bypasses_model_loop(self):
        calendar = object()
        runtime = SimpleNamespace(cal=calendar)
        bridge = SimpleNamespace(run=AsyncMock(return_value="deterministic digest"))
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101),
            message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
        start = datetime(2026, 7, 18, tzinfo=config().tz)
        end = datetime(2026, 7, 19, tzinfo=config().tz)

        with patch(
            "calbot.telegram_app._components",
            return_value=(runtime, config(), bridge),
        ):
            await _run_digest_command(
                update,
                context,
                "today summary",
                start,
                end,
            )

        self.assertIs(bridge.run.await_args.args[0].__name__, "create_calendar_digest")
        self.assertEqual(bridge.run.await_args.kwargs["calendar_client"], calendar)
        message.reply_text.assert_awaited_once_with("deterministic digest")


if __name__ == "__main__":
    unittest.main()
