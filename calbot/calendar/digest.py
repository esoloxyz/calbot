"""Reliable, history-free calendar digests for scheduled Telegram messages."""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("calendar-digest")
MAX_DIGEST_EVENTS = 200
MAX_DIGEST_PAGES = 20
MAX_DIGEST_CHARS = 8000
MAX_DIGEST_FIELD_CHARS = 200


def _safe_page(raw: str) -> tuple[list[dict], str, bool]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Calendar returned an invalid response")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ValueError("Calendar events must be a list")
    next_page_token = payload.get("next_page_token", "")
    if not isinstance(next_page_token, str) or len(next_page_token) > 2048:
        raise ValueError("Calendar returned an invalid next-page token")

    def one_line(value, *, default: str = "") -> str:
        normalized = " ".join(str(value or default).split())
        if len(normalized) <= MAX_DIGEST_FIELD_CHARS:
            return normalized
        return normalized[: MAX_DIGEST_FIELD_CHARS - 1] + "…"

    safe_events = []
    for event in events:
        if not isinstance(event, dict):
            continue
        safe_events.append(
            {
                "title": one_line(event.get("title"), default="(no title)"),
                "start": one_line(event.get("start")),
                "end": one_line(event.get("end")),
                "location": one_line(event.get("location")),
            }
        )
    return (
        safe_events,
        next_page_token,
        bool(payload.get("truncated") or next_page_token),
    )


def _fetch_events(
    calendar_client, start: datetime, end: datetime
) -> tuple[list[dict], bool]:
    events: list[dict] = []
    page_token = ""
    seen_tokens = {page_token}

    for _ in range(MAX_DIGEST_PAGES):
        raw = calendar_client.list_events(
            start.isoformat(), end.isoformat(), page_token=page_token
        )
        page_events, next_page_token, page_truncated = _safe_page(raw)
        remaining = MAX_DIGEST_EVENTS - len(events)
        events.extend(page_events[:remaining])

        if len(page_events) > remaining:
            return events, True
        if not next_page_token:
            return events, page_truncated
        if len(events) >= MAX_DIGEST_EVENTS:
            return events, True
        if next_page_token in seen_tokens:
            log.warning("Calendar pagination repeated a page token; stopping safely")
            return events, True

        seen_tokens.add(next_page_token)
        page_token = next_page_token

    log.warning("Calendar pagination exceeded the page safety limit")
    return events, True


def _friendly_start(value: str, timezone: str) -> str:
    if not value:
        return "Time not specified"
    if "T" not in value:
        day = datetime.fromisoformat(value).strftime("%a, %b %d").replace(" 0", " ")
        return f"{day} (all day)"

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone(ZoneInfo(timezone))
    day = parsed.strftime("%a, %b %d").replace(" 0", " ")
    clock = parsed.strftime("%I:%M %p").lstrip("0")
    return f"{day} at {clock}"


def _truncation_notice(event_count: int) -> str:
    noun = "event" if event_count == 1 else "events"
    return (
        "More events may exist; this digest shows only the first "
        f"{event_count} {noun} fetched safely."
    )


def _fallback_digest(
    label: str, events: list[dict], timezone: str, truncated: bool
) -> str:
    if not events:
        if truncated:
            return "\n".join(
                [
                    f"No events were fetched for your {label}.",
                    _truncation_notice(0),
                ]
            )
        return f"Your {label} is clear — nothing is scheduled."

    lines = [f"Your {label}:"]
    rendered_count = 0
    # Reserve room for authoritative omission/truncation notices.
    notice_reserve = 300
    for event in events:
        line = f"• {_friendly_start(event['start'], timezone)} — {event['title']}"
        if event["location"]:
            line += f" ({event['location']})"
        candidate = "\n".join([*lines, line])
        if len(candidate) + notice_reserve > MAX_DIGEST_CHARS:
            break
        lines.append(line)
        rendered_count += 1
    omitted = len(events) - rendered_count
    if omitted:
        noun = "event" if omitted == 1 else "events"
        lines.append(
            f"{omitted} additional fetched {noun} omitted to keep this message bounded."
        )
    if truncated:
        lines.append(_truncation_notice(len(events)))
    return "\n".join(lines)[:MAX_DIGEST_CHARS]


def create_calendar_digest(
    calendar_client,
    label: str,
    start: datetime,
    end: datetime,
    timezone: str,
) -> str:
    """Fetch and format a complete, deterministic digest without model inference."""
    events, truncated = _fetch_events(calendar_client, start, end)
    log.info("Fetched %s event(s) for %s (truncated=%s)", len(events), label, truncated)
    return _fallback_digest(label, events, timezone, truncated)
