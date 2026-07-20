"""One-shot, actor-bound approvals for assistant side effects."""

from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional


APPROVAL_TTL = timedelta(minutes=10)
ActorKey = tuple[int, int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fixed_decimal(value: str, *, allow_zero: bool = False) -> str:
    if len(str(value)) > 64:
        raise ValueError("approval amount is too long")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("approval amount must be a valid decimal") from exc
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"approval amount must be {comparison}")
    _, digits, exponent = amount.as_tuple()
    if max(0, -exponent) > 6 or max(1, len(digits) + exponent) > 18:
        raise ValueError("approval amount exceeds supported precision")
    whole, _, fraction = format(amount, "f").partition(".")
    fraction = fraction.rstrip("0").ljust(2, "0")
    return f"{whole}.{fraction}"


@dataclass(frozen=True)
class PendingAction:
    """An exact side effect proposed for one Telegram chat member."""

    actor: ActorKey
    tool_name: str
    tool_args: dict
    created_at: datetime
    amount: str = ""
    spend_limit: str | None = None
    amount_is_maximum: bool = False
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
        return normalized == self.confirmation_prompt.casefold()


class PendingActionStore:
    """Thread-safe in-memory state for one-shot side-effect approvals."""

    def __init__(self):
        self._pending: dict[ActorKey, PendingAction] = {}
        self._lock = threading.RLock()

    def propose(
        self,
        *,
        actor: ActorKey,
        tool_name: str,
        tool_args: dict,
        amount: str = "",
        spend_limit: str | None = None,
        amount_is_maximum: bool = False,
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
            amount=_fixed_decimal(amount) if amount else "",
            spend_limit=(
                _fixed_decimal(spend_limit, allow_zero=True)
                if spend_limit is not None
                else None
            ),
            amount_is_maximum=bool(amount_is_maximum),
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
        """Consume an exact approval, or cancel on the owner's next other message."""
        with self._lock:
            pending = self._pending.get(actor)
            if pending is None:
                return None
            self._pending.pop(actor, None)
            if pending.matches(text, now=now):
                return pending
            return None
