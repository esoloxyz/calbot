"""Short-lived, exact confirmations for paid Tempo tool calls."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional


APPROVAL_TTL = timedelta(minutes=10)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_amount(value: str) -> Optional[Decimal]:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        return None
    return amount if amount > 0 else None


@dataclass(frozen=True)
class PendingPaymentApproval:
    tool_args: dict
    amount: str
    created_at: datetime

    @classmethod
    def from_tool_result(
        cls, tool_args: dict, output: str, now: Optional[datetime] = None
    ) -> Optional["PendingPaymentApproval"]:
        try:
            result = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(result, dict):
            return None
        if result.get("error_code") != "confirmation_required":
            return None
        amount = result.get("approval_amount")
        normalized = _normalize_amount(amount)
        if normalized is None:
            return None
        return cls(
            tool_args=dict(tool_args),
            amount=f"{normalized:.2f}",
            created_at=now or _utc_now(),
        )

    @property
    def confirmation_prompt(self) -> str:
        return f"approve ${self.amount}"

    def expired(self, now: Optional[datetime] = None) -> bool:
        return (now or _utc_now()) - self.created_at > APPROVAL_TTL

    def matches(self, text: str, now: Optional[datetime] = None) -> bool:
        if self.expired(now):
            return False
        match = re.fullmatch(
            r"\s*approve\s+\$?([0-9]+(?:\.[0-9]{1,6})?)\s*",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return False
        requested = _normalize_amount(match.group(1))
        expected = _normalize_amount(self.amount)
        return requested is not None and requested == expected
