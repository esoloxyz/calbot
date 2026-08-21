"""Durable conversation state, idempotent replies, and calendar action receipts."""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from collections.abc import Iterable


MAX_CONTEXT_MESSAGES = 24
MAX_RECEIPTS = 12
log = logging.getLogger("assistant-bot")


class InMemoryStateStore:
    """Process-local state for tests and development without a database."""

    def __init__(self):
        self._messages: dict[int, deque[dict]] = defaultdict(deque)
        self._receipts: dict[int, deque[dict]] = defaultdict(deque)
        self._replies: dict[str, str] = {}
        self._lock = threading.RLock()

    def recent_messages(self, chat_id: int, limit: int = MAX_CONTEXT_MESSAGES) -> list:
        with self._lock:
            return list(self._messages[chat_id])[-limit:]

    def record_turn(
        self,
        *,
        chat_id: int,
        user_id: int,
        actor_name: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        with self._lock:
            messages = self._messages[chat_id]
            messages.extend(
                (
                    {
                        "role": "user",
                        "content": user_text,
                        "actor_name": actor_name,
                        "user_id": user_id,
                    },
                    {"role": "assistant", "content": assistant_text},
                )
            )
            while len(messages) > MAX_CONTEXT_MESSAGES:
                messages.popleft()

    def recent_receipts(self, chat_id: int, limit: int = MAX_RECEIPTS) -> list[dict]:
        with self._lock:
            return list(self._receipts[chat_id])[-limit:]

    def record_receipts(
        self,
        *,
        chat_id: int,
        user_id: int,
        request_id: str,
        receipts: Iterable[dict],
    ) -> None:
        with self._lock:
            target = self._receipts[chat_id]
            for index, receipt in enumerate(receipts, start=1):
                target.append(
                    {
                        **dict(receipt),
                        "user_id": user_id,
                        "request_id": request_id,
                        "action_index": index,
                    }
                )
            while len(target) > MAX_RECEIPTS:
                target.popleft()

    def cached_reply(self, request_id: str) -> str | None:
        with self._lock:
            return self._replies.get(request_id) if request_id else None

    def cache_reply(self, request_id: str, reply: str) -> None:
        if request_id:
            with self._lock:
                self._replies.setdefault(request_id, reply)


class PostgresStateStore:
    """Small Postgres-backed ledger shared safely across deploys and workers."""

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("database_url is required")
        try:
            import psycopg
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("psycopg is required when DATABASE_URL is set") from exc
        self._psycopg = psycopg
        self.database_url = database_url
        self._initialize()

    def _connect(self):
        return self._psycopg.connect(self.database_url)

    def _initialize(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS calbot_messages (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT,
                actor_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            """CREATE INDEX IF NOT EXISTS calbot_messages_chat_id_id
               ON calbot_messages (chat_id, id DESC)""",
            """CREATE TABLE IF NOT EXISTS calbot_action_receipts (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                request_id TEXT NOT NULL,
                action_index INTEGER NOT NULL,
                receipt JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (request_id, action_index)
            )""",
            """CREATE INDEX IF NOT EXISTS calbot_receipts_chat_id_id
               ON calbot_action_receipts (chat_id, id DESC)""",
            """CREATE TABLE IF NOT EXISTS calbot_processed_requests (
                request_id TEXT PRIMARY KEY,
                reply TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        log.info("Postgres state store ready")

    def recent_messages(self, chat_id: int, limit: int = MAX_CONTEXT_MESSAGES) -> list:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT role, content, actor_name, user_id
                       FROM calbot_messages WHERE chat_id = %s
                       ORDER BY id DESC LIMIT %s""",
                    (chat_id, limit),
                )
                rows = cursor.fetchall()
        messages = []
        for role, content, actor_name, user_id in reversed(rows):
            message = {"role": role, "content": content}
            if role == "user":
                message.update({"actor_name": actor_name, "user_id": user_id})
            messages.append(message)
        return messages

    def record_turn(
        self,
        *,
        chat_id: int,
        user_id: int,
        actor_name: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO calbot_messages
                       (chat_id, user_id, actor_name, role, content)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        (chat_id, user_id, actor_name, "user", user_text),
                        (chat_id, None, "", "assistant", assistant_text),
                    ),
                )

    def recent_receipts(self, chat_id: int, limit: int = MAX_RECEIPTS) -> list[dict]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT receipt, user_id, request_id, action_index
                       FROM calbot_action_receipts WHERE chat_id = %s
                       ORDER BY id DESC LIMIT %s""",
                    (chat_id, limit),
                )
                rows = cursor.fetchall()
        receipts = []
        for receipt, user_id, request_id, action_index in reversed(rows):
            parsed = receipt if isinstance(receipt, dict) else json.loads(receipt)
            receipts.append(
                {
                    **parsed,
                    "user_id": user_id,
                    "request_id": request_id,
                    "action_index": action_index,
                }
            )
        return receipts

    def record_receipts(
        self,
        *,
        chat_id: int,
        user_id: int,
        request_id: str,
        receipts: Iterable[dict],
    ) -> None:
        rows = [
            (chat_id, user_id, request_id, index, json.dumps(dict(receipt)))
            for index, receipt in enumerate(receipts, start=1)
        ]
        if not rows:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO calbot_action_receipts
                       (chat_id, user_id, request_id, action_index, receipt)
                       VALUES (%s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (request_id, action_index) DO NOTHING""",
                    rows,
                )

    def cached_reply(self, request_id: str) -> str | None:
        if not request_id:
            return None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT reply FROM calbot_processed_requests WHERE request_id = %s",
                    (request_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def cache_reply(self, request_id: str, reply: str) -> None:
        if not request_id:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO calbot_processed_requests (request_id, reply)
                       VALUES (%s, %s) ON CONFLICT (request_id) DO NOTHING""",
                    (request_id, reply),
                )


def create_state_store(database_url: str = ""):
    """Choose durable production state when configured, otherwise local state."""
    return PostgresStateStore(database_url) if database_url else InMemoryStateStore()
