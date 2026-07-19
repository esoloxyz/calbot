import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from bot import (
    _authorized,
    _run_digest_command,
    _weekend_window,
    cmd_balance,
    on_message,
    scheduled_digest,
    telegram_chunks,
)
from bot_runtime import BotConfig
from calendar_digest import create_calendar_digest


class TelegramDeliveryTests(unittest.TestCase):
    def test_long_unicode_reply_is_split_without_data_loss(self):
        reply = "🙂" * 5001

        chunks = telegram_chunks(reply)

        self.assertEqual("".join(chunks), reply)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))

    def test_empty_reply_has_no_delivery_chunks(self):
        self.assertEqual(telegram_chunks(""), [])


class MessageAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = BotConfig(
            telegram_token="telegram-token",
            anthropic_api_key="anthropic-key",
            allowed_chat_id=-100123,
            respond_to_all=False,
        )

    def test_anonymous_admin_sender_is_rejected(self):
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=1087968824),
            message=SimpleNamespace(sender_chat=SimpleNamespace(id=-100123)),
        )

        self.assertFalse(_authorized(update, self.config))

    async def test_mixed_case_mention_is_removed_before_assistant_call(self):
        message = SimpleNamespace(
            text="@CalBot Help me",
            chat_id=-100123,
            message_id=7,
            sender_chat=None,
            reply_to_message=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101, first_name="Ezra"),
            message=message,
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(
                username="calbot",
                id=999,
                send_chat_action=AsyncMock(),
            )
        )

        with (
            patch("bot._components", return_value=(object(), self.config, object())),
            patch("bot._ask", AsyncMock(return_value="response")) as ask,
        ):
            await on_message(update, context)

        self.assertEqual(ask.await_args.args[2], "Help me")
        message.reply_text.assert_awaited_once_with("response")

    async def test_mention_only_message_does_not_call_assistant(self):
        message = SimpleNamespace(
            text="@CALBOT",
            chat_id=-100123,
            message_id=8,
            sender_chat=None,
            reply_to_message=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101, first_name="Ezra"),
            message=message,
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(
                username="calbot",
                id=999,
                send_chat_action=AsyncMock(),
            )
        )

        with (
            patch("bot._components", return_value=(object(), self.config, object())),
            patch("bot._ask", AsyncMock()) as ask,
        ):
            await on_message(update, context)

        ask.assert_not_awaited()
        context.bot.send_chat_action.assert_not_awaited()


class CalendarWindowTests(unittest.TestCase):
    def test_weekend_window_uses_the_current_weekend_on_friday_through_sunday(self):
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

    def test_weekend_window_uses_the_coming_friday_during_the_workweek(self):
        timezone = ZoneInfo("America/New_York")

        start, end = _weekend_window(datetime(2026, 7, 13, 15, 30, tzinfo=timezone))

        self.assertEqual(start, datetime(2026, 7, 17, tzinfo=timezone))
        self.assertEqual(end, datetime(2026, 7, 20, tzinfo=timezone))


class DigestCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_balance_command_bypasses_the_model_loop(self):
        config = BotConfig(
            telegram_token="telegram-token",
            anthropic_api_key="anthropic-key",
            allowed_chat_id=-100123,
        )
        runtime = SimpleNamespace(wallet_balance_reply=lambda: "wallet balance")
        bridge = SimpleNamespace(run=AsyncMock(return_value="wallet balance"))
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101),
            message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

        with patch("bot._components", return_value=(runtime, config, bridge)):
            await cmd_balance(update, context)

        self.assertIs(bridge.run.await_args.args[0], runtime.wallet_balance_reply)
        message.reply_text.assert_awaited_once_with("wallet balance")

    async def test_command_uses_the_shared_deterministic_digest_path(self):
        config = BotConfig(
            telegram_token="telegram-token",
            anthropic_api_key="anthropic-key",
            allowed_chat_id=-100123,
            timezone="America/New_York",
        )
        calendar = object()
        runtime = SimpleNamespace(cal=calendar)
        bridge = SimpleNamespace(run=AsyncMock(return_value="deterministic digest"))
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123),
            effective_user=SimpleNamespace(id=101),
            message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
        start = datetime(2026, 7, 18, tzinfo=config.tz)
        end = datetime(2026, 7, 19, tzinfo=config.tz)

        with patch(
            "bot._components",
            return_value=(runtime, config, bridge),
        ):
            await _run_digest_command(
                update,
                context,
                "today summary",
                start,
                end,
            )

        call = bridge.run.await_args
        self.assertIs(call.args[0], create_calendar_digest)
        self.assertEqual(call.kwargs["calendar_client"], calendar)
        self.assertNotIn("claude_client", call.kwargs)
        self.assertNotIn("model", call.kwargs)
        message.reply_text.assert_awaited_once_with("deterministic digest")

    async def test_scheduled_digest_uses_the_current_digest_signature(self):
        config = BotConfig(
            telegram_token="telegram-token",
            anthropic_api_key="anthropic-key",
            allowed_chat_id=-100123,
            timezone="America/New_York",
        )
        calendar = object()
        runtime = SimpleNamespace(cal=calendar)
        bridge = SimpleNamespace(run=AsyncMock(return_value="scheduled digest"))
        context = SimpleNamespace(
            job=SimpleNamespace(data="weekend"),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )

        with (
            patch("bot._components", return_value=(runtime, config, bridge)),
            patch("bot.datetime") as mocked_datetime,
        ):
            mocked_datetime.now.return_value = datetime(
                2026, 7, 17, 9, 0, tzinfo=config.tz
            )
            await scheduled_digest(context)

        call = bridge.run.await_args
        self.assertIs(call.args[0], create_calendar_digest)
        self.assertNotIn("claude_client", call.kwargs)
        self.assertNotIn("model", call.kwargs)
        context.bot.send_message.assert_awaited_once_with(
            chat_id=-100123,
            text="scheduled digest",
        )


if __name__ == "__main__":
    unittest.main()
    (cmd_balance,)
