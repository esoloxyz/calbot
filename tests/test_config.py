import unittest

from calbot.config import BotConfig


class BotConfigTests(unittest.TestCase):
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

    def test_invalid_timezone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid IANA timezone"):
            BotConfig.from_env(
                {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "ANTHROPIC_API_KEY": "key",
                    "ALLOWED_CHAT_ID": "-100123",
                    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                    "CALENDAR_ID": "shared@example.com",
                    "TIMEZONE": "not/a-timezone",
                }
            )


if __name__ == "__main__":
    unittest.main()
