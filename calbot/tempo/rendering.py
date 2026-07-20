"""Plain-English rendering for Tempo approvals, balances, and provider results."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

from calbot.tempo.balances import is_fiat_currency


MINIMUM_VISIBLE_BALANCE = Decimal("0.50")
MAX_VISIBLE_FIELD_CHARS = 800
MAX_SYNTHESIS_ITEMS = 8

_PROVIDER_NAMES = {
    "exa.mpp.tempo.xyz": "Exa",
    "parallelmpp.dev": "Parallel",
    "fal.mpp.tempo.xyz": "Fal",
    "openai.mpp.tempo.xyz": "OpenAI",
}
_SEARCH_TEXT_FIELDS = (
    "summary",
    "snippet",
    "description",
    "excerpt",
    "excerpts",
    "text",
    "content",
)


def plain_text(value: object, *, limit: int = MAX_VISIBLE_FIELD_CHARS) -> str:
    """Collapse provider text and ensure raw object delimiters never reach Telegram."""
    text = str(value or "").replace("{", "(").replace("}", ")")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _display_amount(amount: Decimal) -> str:
    precision = Decimal("0.000001") if abs(amount) < 1 else Decimal("0.01")
    rendered = format(amount.quantize(precision, rounding=ROUND_HALF_UP), "f")
    return rendered.rstrip("0").rstrip(".")


def _parse_amount(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("1e30"):
        return None
    return amount


def render_wallet_balances(output: str) -> str:
    """Render every stablecoin balance strictly above the visible threshold."""
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return "I couldn't read your Tempo wallet balance."
    if not isinstance(payload, dict) or payload.get("error"):
        return "I couldn't read your Tempo wallet balance."

    candidates = payload.get("balances")
    if not isinstance(candidates, list):
        legacy = payload.get("balance")
        candidates = [legacy] if isinstance(legacy, dict) else []

    totals: dict[str, Decimal] = {}
    for balance in candidates:
        if not isinstance(balance, dict):
            continue
        symbol = balance.get("symbol")
        currency = str(balance.get("currency", "")).upper()
        amount = _parse_amount(
            balance.get("amount", balance.get("available", balance.get("total")))
        )
        if (
            not isinstance(symbol, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", symbol)
            or amount is None
            or amount <= MINIMUM_VISIBLE_BALANCE
            or (currency and not is_fiat_currency(currency))
        ):
            continue
        totals[symbol] = totals.get(symbol, Decimal("0")) + amount

    if not totals:
        return "Your Tempo wallet has no stablecoin balances above $0.50."
    balances = sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))
    lines = [f"• ${_display_amount(amount)} {symbol}" for symbol, amount in balances]
    if len(lines) == 1:
        return "Your Tempo wallet balance is " + lines[0][2:] + "."
    return "Your Tempo wallet balances are:\n" + "\n".join(lines)


def _body_fields(tool_args: dict) -> dict:
    body = tool_args.get("body", "")
    if not isinstance(body, str) or not body:
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def render_service_approval(
    tool_args: dict,
    *,
    amount: str = "",
    amount_is_maximum: bool = False,
    spend_limit: str | None = None,
) -> str:
    """Describe an exact service call without exposing transport-level call data."""
    url = str(tool_args.get("url", ""))
    parsed = urlsplit(url)
    provider = _PROVIDER_NAMES.get(
        parsed.hostname or "", parsed.hostname or "the service"
    )
    fields = _body_fields(tool_args)
    query = fields.get("query")
    research_input = fields.get("input")
    prompt = fields.get("prompt")
    if isinstance(query, str) and query.strip():
        action = f"search the web for “{plain_text(query, limit=400)}”"
    elif isinstance(research_input, str) and research_input.strip():
        action = f"research “{plain_text(research_input, limit=400)}”"
    elif isinstance(prompt, str) and prompt.strip():
        subject = plain_text(prompt, limit=400)
        action = (
            f"generate an image for “{subject}”"
            if "image" in parsed.path.casefold() or provider == "Fal"
            else f"process the prompt “{subject}”"
        )
    elif "extract" in parsed.path.casefold():
        action = "read the requested web pages"
    else:
        action = "send the requested information"

    if amount:
        qualifier = "cost up to" if amount_is_maximum else "cost"
        price = f"This will {qualifier} ${plain_text(amount, limit=40)}."
    elif spend_limit == "0.00":
        price = "This request is free."
    else:
        price = "This request will not spend more than the approved limit."
    return (
        f"I'm ready to {action} using {plain_text(provider, limit=100)}. "
        f"{price}\nReply approve to continue."
    )


def provider_message(payload: dict) -> str:
    for key in ("message", "error", "detail", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return plain_text(value)
    return "The provider did not include a readable explanation."


def _result_summary(item: dict) -> str:
    for key in _SEARCH_TEXT_FIELDS:
        value = item.get(key)
        if isinstance(value, list):
            value = " ".join(str(part) for part in value[:3])
        if isinstance(value, str) and value.strip():
            return plain_text(value)
    return ""


def compact_external_payload(payload: object) -> tuple[object, str]:
    """Build a small synthesis payload and a deterministic plain-text fallback."""
    if isinstance(payload, dict):
        for key in ("answer", "output", "result", "content", "text", "summary"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                stripped = value.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        return compact_external_payload(json.loads(stripped))
                    except json.JSONDecodeError:
                        pass
                rendered = plain_text(value, limit=2800)
                return {key: rendered}, rendered
            if isinstance(value, (dict, list)):
                return compact_external_payload(value)
        results = payload.get("results")
        if isinstance(results, list):
            return compact_external_payload(results)
        useful = {
            key: plain_text(value)
            for key, value in payload.items()
            if isinstance(value, (str, int, float, bool))
            and key
            not in {
                "requestId",
                "request_id",
                "resolvedSearchType",
                "run_id",
                "status",
                "task_run_id",
                "url",
                "method",
                "body",
                "headers",
                "max_spend",
            }
        }
        if useful:
            fallback = "\n".join(
                f"{plain_text(key).replace('_', ' ').title()}: {value}"
                for key, value in useful.items()
            )
            return useful, fallback
        return (
            {},
            "The request completed, but the provider returned no readable answer.",
        )

    if isinstance(payload, list):
        if not payload:
            return [], "I couldn't find any matching results."
        compact = []
        lines = ["I found these results:"]
        for index, item in enumerate(payload[:MAX_SYNTHESIS_ITEMS], start=1):
            if not isinstance(item, dict):
                text = plain_text(item)
                if text:
                    compact.append({"text": text})
                    lines.append(f"{index}. {text}")
                continue
            title = plain_text(item.get("title") or item.get("name") or "Result")
            url = item.get("url") or item.get("id")
            url = (
                plain_text(url, limit=2048)
                if isinstance(url, str) and url.startswith(("https://", "http://"))
                else ""
            )
            summary = _result_summary(item)
            compact_item = {"title": title}
            if summary:
                compact_item["summary"] = summary
            if url:
                compact_item["url"] = url
            compact.append(compact_item)
            lines.append(f"{index}. {title}")
            if summary:
                lines.append(f"   {summary}")
            if url:
                lines.append(f"   {url}")
        return compact, "\n".join(lines)

    rendered = plain_text(payload, limit=2800)
    if rendered:
        return rendered, rendered
    return "", "The request completed, but the provider returned no readable answer."
