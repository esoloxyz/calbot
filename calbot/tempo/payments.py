"""Money, pricing, and per-request authorization primitives for Tempo calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple


MAX_MONEY_TEXT_CHARS = 64
# tempo-request v0.6.5 interprets --max-spend with six decimal places. Accepting
# finer precision would let its parser round a reviewed cap upward.
MAX_MONEY_DECIMAL_PLACES = 6
MAX_MONEY_INTEGER_DIGITS = 18


def _bounded_money_decimal(value, *, label: str = "amount") -> Decimal:
    rendered = str(value)
    if len(rendered) > MAX_MONEY_TEXT_CHARS:
        raise ValueError(f"{label} is too long")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(rendered)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a valid decimal amount") from exc
    if not amount.is_finite():
        raise ValueError(f"{label} must be finite")

    _, raw_digits, raw_exponent = amount.as_tuple()
    digits = list(raw_digits)
    exponent = raw_exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    decimal_places = max(0, -exponent)
    integer_digits = max(1, len(digits) + exponent)
    if (
        len(digits) > MAX_MONEY_INTEGER_DIGITS + MAX_MONEY_DECIMAL_PLACES
        or decimal_places > MAX_MONEY_DECIMAL_PLACES
        or integer_digits > MAX_MONEY_INTEGER_DIGITS
    ):
        raise ValueError(
            f"{label} must use at most {MAX_MONEY_INTEGER_DIGITS} integer digits "
            f"and {MAX_MONEY_DECIMAL_PLACES} decimal places"
        )
    return amount


def decimal_text(value: Decimal) -> str:
    value = _bounded_money_decimal(value)
    whole, _, fraction = format(value, "f").partition(".")
    fraction = fraction.rstrip("0").ljust(2, "0")
    return f"{whole}.{fraction}"


def _canonical_body(body: str) -> str:
    try:
        return json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return (body or "").strip()


def _call_fingerprint(args: dict) -> Tuple[str, str, str, str]:
    max_spend = args.get("max_spend", "")
    try:
        max_spend = str(_bounded_money_decimal(max_spend)) if max_spend else ""
    except (InvalidOperation, ValueError):
        max_spend = str(max_spend)
    return (
        str(args.get("url", "")),
        str(args.get("method", "POST")).upper(),
        _canonical_body(str(args.get("body", ""))),
        max_spend,
    )


def _unknown_payment_submission() -> str:
    return json.dumps(
        {
            "error": (
                "The payment request stopped before its final response was "
                "confirmed. Its outcome is unknown; do not retry it."
            ),
            "error_code": "payment_submission_outcome_unknown",
        }
    )


@dataclass(frozen=True)
class EndpointPayment:
    amount: Optional[Decimal]
    dynamic: bool
    free: bool = False


@dataclass(frozen=True)
class TempoCallPreview:
    """Validated service-call metadata produced without submitting a payment."""

    call_args: dict
    amount: Decimal = Decimal("0")
    spend_limit: str = ""
    requires_confirmation: bool = False
    price_is_maximum: bool = False
    trusted_nonpaying_poll: bool = False
    error: Optional[dict] = None


class TempoRequestBudget:
    """Cumulative authorization state for one user-visible bot request."""

    def __init__(
        self,
        auto_limit: str = "0.01",
        approved_call: Optional[dict] = None,
        approved_limit: Optional[str] = None,
    ):
        self.auto_limit = _bounded_money_decimal(auto_limit, label="auto spend limit")
        if self.auto_limit <= 0:
            raise ValueError("auto spend limit must be greater than zero")
        self.approved_fingerprint = (
            _call_fingerprint(approved_call) if approved_call else None
        )
        if approved_limit is None:
            self.approved_limit = self.auto_limit
        else:
            self.approved_limit = _bounded_money_decimal(
                approved_limit, label="approved spend limit"
            )
            if self.approved_limit < 0:
                raise ValueError("approved spend limit must be non-negative")
        self.spent = Decimal("0")
        self.paid_request_submitted = False
        self.confirmation_pending = False

    def authorize(
        self,
        call_args: dict,
        amount: Decimal,
        requires_confirmation: bool,
    ) -> Optional[dict]:
        if amount == 0:
            return None
        if self.paid_request_submitted:
            return {
                "error": (
                    "A paid request was already submitted for this Telegram message. "
                    "It will not be retried automatically."
                ),
                "error_code": "paid_request_already_submitted",
            }

        approved = (
            self.approved_fingerprint is not None
            and _call_fingerprint(call_args) == self.approved_fingerprint
        )
        if self.confirmation_pending:
            return {
                "error": (
                    "A paid request is already waiting for explicit approval for "
                    "this Telegram message."
                ),
                "error_code": "payment_confirmation_pending",
            }
        if self.approved_fingerprint is not None and not approved:
            return {
                "error": (
                    "This approval is scoped to one exact paid request; a different "
                    "request was not submitted."
                ),
                "error_code": "approval_scope_mismatch",
            }
        limit = self.approved_limit if approved else self.auto_limit
        if requires_confirmation or amount > self.auto_limit:
            if not approved or amount > self.approved_limit:
                amount_text = decimal_text(amount)
                self.confirmation_pending = True
                return {
                    "error": (
                        "This paid request requires explicit user confirmation before "
                        "any payment is submitted."
                    ),
                    "error_code": "confirmation_required",
                    "approval_amount": amount_text,
                    "confirmation_prompt": "approve",
                }
        if self.spent + amount > limit:
            return {
                "error": (
                    f"This request would exceed the cumulative ${decimal_text(limit)} "
                    "budget for the current Telegram message."
                ),
                "error_code": "cumulative_budget_exceeded",
            }
        return None

    def mark_submitted(self, amount: Decimal) -> None:
        if amount > 0:
            self.spent += amount
            self.paid_request_submitted = True
