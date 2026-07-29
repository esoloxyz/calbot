"""Validated environment configuration for Calbot."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str = field(repr=False)
    anthropic_api_key: str = field(repr=False)
    allowed_chat_id: int
    timezone: str = DEFAULT_TIMEZONE
    model: str = DEFAULT_MODEL
    bot_owner: str = "there"
    respond_to_all: bool = True
    google_service_account_json: str = field(default="", repr=False)
    calendar_id: str = ""
    allowed_user_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BotConfig":
        values = os.environ if env is None else env
        required = (
            "TELEGRAM_BOT_TOKEN",
            "ANTHROPIC_API_KEY",
            "ALLOWED_CHAT_ID",
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "CALENDAR_ID",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        try:
            allowed_chat_id = int(values["ALLOWED_CHAT_ID"])
        except ValueError as exc:
            raise ValueError("ALLOWED_CHAT_ID must be an integer") from exc

        timezone = values.get("TIMEZONE", DEFAULT_TIMEZONE)
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(
                f"TIMEZONE is not a valid IANA timezone: {timezone}"
            ) from exc

        respond_to_all = values.get("RESPOND_TO_ALL", "true").strip().casefold()
        if respond_to_all not in {"true", "false"}:
            raise ValueError("RESPOND_TO_ALL must be true or false")

        try:
            allowed_users = frozenset(
                int(value.strip())
                for value in values.get("ALLOWED_USER_IDS", "").split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise ValueError("ALLOWED_USER_IDS must contain only integers") from exc

        return cls(
            telegram_token=values["TELEGRAM_BOT_TOKEN"],
            anthropic_api_key=values["ANTHROPIC_API_KEY"],
            allowed_chat_id=allowed_chat_id,
            timezone=timezone,
            model=values.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
            bot_owner=values.get("BOT_OWNER", "there"),
            respond_to_all=respond_to_all == "true",
            google_service_account_json=values["GOOGLE_SERVICE_ACCOUNT_JSON"],
            calendar_id=values["CALENDAR_ID"],
            allowed_user_ids=allowed_users,
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def actor_allowed(self, user_id: int) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids
