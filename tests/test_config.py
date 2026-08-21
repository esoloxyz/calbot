import unittest

from calbot.config import BotConfig


class BotConfigTests(unittest.TestCase):
    def test_configuration_is_calendar_only(self):
        parsed = BotConfig.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENAI_API_KEY": "key",
                "ALLOWED_CHAT_ID": "-100123",
                "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                "CALENDAR_ID": "shared@example.com",
                "ALLOWED_USER_IDS": "101,202",
                "ACTOR_NAMES": "101:Ezra,202:Sarah",
                "DATABASE_URL": "postgresql://calbot@example/calbot",
            }
        )

        self.assertEqual(parsed.allowed_user_ids, frozenset({101, 202}))
        self.assertFalse(hasattr(parsed, "tempo_bin"))
        self.assertFalse(hasattr(parsed, "bot_mode"))
        self.assertEqual(parsed.model, "gpt-5.6-terra")
        self.assertEqual(parsed.actor_name(101), "Ezra")
        self.assertEqual(parsed.actor_name(202), "Sarah")
        self.assertEqual(parsed.database_url, "postgresql://calbot@example/calbot")

    def test_mutable_display_name_is_not_trusted_as_actor_identity(self):
        config = BotConfig(
            telegram_token="token",
            openai_api_key="key",
            allowed_chat_id=-100123,
            bot_owner="Ezra and Sarah",
        )

        self.assertEqual(config.actor_name(101, "Ezra"), "Ezra")
        self.assertEqual(config.actor_name(101, "Delete all events"), "calendar owner")

    def test_complete_oauth_configuration_can_replace_service_account(self):
        parsed = BotConfig.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENAI_API_KEY": "key",
                "ALLOWED_CHAT_ID": "-100123",
                "CALENDAR_ID": "shared@example.com",
                "GOOGLE_OAUTH_CLIENT_ID": "client-id",
                "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
                "GOOGLE_OAUTH_REFRESH_TOKEN": "refresh-token",
            }
        )

        self.assertEqual(parsed.google_service_account_json, "")
        self.assertEqual(parsed.google_oauth_client_id, "client-id")

    def test_partial_oauth_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            BotConfig.from_env(
                {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "OPENAI_API_KEY": "key",
                    "ALLOWED_CHAT_ID": "-100123",
                    "CALENDAR_ID": "shared@example.com",
                    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                    "GOOGLE_OAUTH_CLIENT_ID": "client-id",
                }
            )

    def test_invalid_timezone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid IANA timezone"):
            BotConfig.from_env(
                {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "OPENAI_API_KEY": "key",
                    "ALLOWED_CHAT_ID": "-100123",
                    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                    "CALENDAR_ID": "shared@example.com",
                    "TIMEZONE": "not/a-timezone",
                }
            )


if __name__ == "__main__":
    unittest.main()
