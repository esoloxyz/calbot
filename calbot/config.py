"""Validated environment configuration for Calbot."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_MODEL = "gpt-5.6-terra"


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str = field(repr=False)
    openai_api_key: str = field(repr=False)
    allowed_chat_id: int
    timezone: str = DEFAULT_TIMEZONE
    model: str = DEFAULT_MODEL
    bot_owner: str = "there"
    respond_to_all: bool = True
    google_service_account_json: str = field(default="", repr=False)
    google_oauth_client_id: str = field(default="", repr=False)
    google_oauth_client_secret: str = field(default="", repr=False)
    google_oauth_refresh_token: str = field(default="", repr=False)
    calendar_id: str = ""
    allowed_user_ids: frozenset[int] = frozenset()
    actor_names: tuple[tuple[int, str], ...] = ()
    database_url: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BotConfig":
        values = os.environ if env is None else env
        required = (
            "TELEGRAM_BOT_TOKEN",
            "OPENAI_API_KEY",
            "ALLOWED_CHAT_ID",
            "CALENDAR_ID",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        service_account_json = values.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        oauth_values = (
            values.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            values.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            values.get("GOOGLE_OAUTH_REFRESH_TOKEN", ""),
        )
        if not service_account_json and not all(oauth_values):
            raise ValueError(
                "Configure GOOGLE_SERVICE_ACCOUNT_JSON or all Google OAuth values"
            )
        if any(oauth_values) and not all(oauth_values):
            raise ValueError("Google OAuth configuration is incomplete")

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

        actor_names = []
        for entry in values.get("ACTOR_NAMES", "").split(","):
            if not entry.strip():
                continue
            try:
                user_id_text, name = entry.split(":", 1)
                user_id = int(user_id_text.strip())
            except (ValueError, TypeError) as exc:
                raise ValueError("ACTOR_NAMES must use user_id:name entries") from exc
            name = name.strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,39}", name):
                raise ValueError("ACTOR_NAMES contains an invalid name")
            actor_names.append((user_id, name))

        return cls(
            telegram_token=values["TELEGRAM_BOT_TOKEN"],
            openai_api_key=values["OPENAI_API_KEY"],
            allowed_chat_id=allowed_chat_id,
            timezone=timezone,
            model=values.get("OPENAI_MODEL", DEFAULT_MODEL),
            bot_owner=values.get("BOT_OWNER", "there"),
            respond_to_all=respond_to_all == "true",
            google_service_account_json=service_account_json,
            google_oauth_client_id=oauth_values[0],
            google_oauth_client_secret=oauth_values[1],
            google_oauth_refresh_token=oauth_values[2],
            calendar_id=values["CALENDAR_ID"],
            allowed_user_ids=allowed_users,
            actor_names=tuple(actor_names),
            database_url=values.get("DATABASE_URL", ""),
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def actor_allowed(self, user_id: int) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids

    def actor_name(self, user_id: int, display_name: str = "") -> str:
        """Return configured identity or a conservative owner-name match."""
        configured = dict(self.actor_names).get(user_id)
        if configured:
            return configured
        candidate = (display_name or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,39}", candidate):
            return "calendar owner"
        owner_words = {
            word.casefold()
            for word in re.findall(r"[A-Za-z][A-Za-z'\-]*", self.bot_owner)
        }
        return candidate if candidate.casefold() in owner_words else "calendar owner"
