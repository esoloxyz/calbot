"""One-shot, actor-bound approvals for calendar changes."""

from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


APPROVAL_TTL = timedelta(minutes=10)
ActorKey = tuple[int, int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PendingAction:
    """An exact calendar change proposed for one Telegram group member."""

    actor: ActorKey
    tool_name: str
    tool_args: dict
    created_at: datetime
    preview: str = ""
    request_text: str = ""

    @property
    def confirmation_prompt(self) -> str:
        return "approve"

    def expired(self, now: Optional[datetime] = None) -> bool:
        return (now or _utc_now()) - self.created_at > APPROVAL_TTL

    def matches(self, text: str, now: Optional[datetime] = None) -> bool:
        if self.expired(now):
            return False
        normalized = re.sub(r"\s+", " ", (text or "").strip()).casefold()
        return normalized == self.confirmation_prompt


class PendingActionStore:
    """Thread-safe in-memory state for one-shot calendar approvals."""

    def __init__(self):
        self._pending: dict[ActorKey, PendingAction] = {}
        self._lock = threading.RLock()

    def propose(
        self,
        *,
        actor: ActorKey,
        tool_name: str,
        tool_args: dict,
        preview: str = "",
        request_text: str = "",
        now: Optional[datetime] = None,
    ) -> PendingAction:
        if len(request_text) > 4000:
            raise ValueError("approval request text is too long")
        pending = PendingAction(
            actor=actor,
            tool_name=tool_name,
            tool_args=copy.deepcopy(tool_args),
            created_at=now or _utc_now(),
            preview=str(preview),
            request_text=str(request_text),
        )
        with self._lock:
            self._pending[actor] = pending
        return pending

    def get(
        self, actor: ActorKey, now: Optional[datetime] = None
    ) -> Optional[PendingAction]:
        with self._lock:
            pending = self._pending.get(actor)
            if pending and pending.expired(now):
                self._pending.pop(actor, None)
                return None
            return pending

    def resolve(
        self,
        actor: ActorKey,
        text: str,
        now: Optional[datetime] = None,
    ) -> Optional[PendingAction]:
        """Consume an exact approval, or cancel on the actor's next other message."""
        with self._lock:
            pending = self._pending.get(actor)
            if pending is None:
                return None
            self._pending.pop(actor, None)
            if pending.matches(text, now=now):
                return pending
            return None
