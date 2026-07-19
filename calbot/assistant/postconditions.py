"""Postconditions for executor-owned assistant claims and calendar replies."""

from __future__ import annotations

import json
import re


CALENDAR_MUTATION_TOOLS = {"create_event", "update_event", "delete_event"}
_CALENDAR_SUBJECT = re.compile(
    r"\b(?:cal(?:endar)?|event|appointment|reservation|wedding|dinner|lunch|"
    r"meeting|party|trip|flight|concert|birthday|plans?)\b",
    re.IGNORECASE,
)
_COMPLETED_ACTION = re.compile(
    r"\b(?:added|created|scheduled|booked|updated|changed|deleted|removed|"
    r"cancelled|canceled|moved|rescheduled|succeeded)\b",
    re.IGNORECASE,
)
_CALENDAR_STATE_COMPLETION = re.compile(
    r"(?:\bcal(?:endar)?\s+action\s+(?:complete|completed|done|finished)\b"
    r"|\b(?:cal(?:endar)?(?:\s+action)?|event|appointment|reservation|wedding|"
    r"dinner|lunch|meeting|party|trip|flight|concert|birthday|plans?)\b"
    r"[^.!?\n]{0,80}?\b(?:is|was|looks|seems)\s+(?:all\s+)?"
    r"(?:complete|completed|done|finished|ready|set|successful|"
    r"good\s+to\s+go)\b)",
    re.IGNORECASE,
)
_CALENDAR_PUT_COMPLETION = re.compile(
    r"\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+put\s+"
    r"(?:"
    r"(?:(?:the|your|my|our)\s+)?(?:event|appointment|reservation|wedding|"
    r"dinner|lunch|meeting|party|trip|flight|concert|birthday|plans?)"
    r"\s+(?:in|into|on)\b"
    r"|[^.!?\n]{1,80}\b(?:in|into|on)\s+"
    r"(?:the|your|my|our)\s+(?:cal(?:endar)?|schedule|diary)\b"
    r")",
    re.IGNORECASE,
)
_CALENDAR_ENTERED_COMPLETION = re.compile(
    r"\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+"
    r"(?:entered|placed|logged)\s+[^.!?\n]{1,80}\b(?:in|into|on)\s+"
    r"(?:the|your|my|our)\s+(?:cal(?:endar)?|schedule|diary)\b",
    re.IGNORECASE,
)
_CALENDAR_INCLUDES_COMPLETION = re.compile(
    r"\b(?:the|your|my|our)\s+cal(?:endar)?\s+now\s+"
    r"(?:includes|contains|shows|has)\b",
    re.IGNORECASE,
)
_CALENDAR_REMOVAL_COMPLETION = re.compile(
    r"(?:\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+"
    r"(?:cleared|taken)\s+[^.!?\n]{1,80}\b(?:from|off)\s+"
    r"(?:the|your|my|our)\s+cal(?:endar)?\b"
    r"|\b(?:the|your|my|our)?\s*(?:event|appointment|reservation|meeting|plans?)"
    r"\s+(?:has|have)\s+been\s+taken\s+off\s+"
    r"(?:the|your|my|our)\s+cal(?:endar)?\b"
    r"|\b(?:the|your|my|our)?\s*(?:event|appointment|reservation|meeting|plans?)"
    r"\s+(?:is|are|was|were)\s+(?:now\s+)?off\s+"
    r"(?:the|your|my|our)\s+cal(?:endar)?\b)",
    re.IGNORECASE,
)
_STANDALONE_PUT_COMPLETION = re.compile(
    r"^\s*(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+put\s+"
    r"(?:it|that|this)\s+in(?:\s+for\s+you)?[.!?\s]*$",
    re.IGNORECASE,
)
_BARE_COMPLETION_ACKNOWLEDGEMENTS = {
    "all set",
    "everything is all set",
    "everything s all set",
    "good to go",
    "it is good to go",
    "it s good to go",
    "we are all set",
    "we re all set",
    "you are all set",
    "you re all set",
    "you are good to go",
    "you re good to go",
}
_PAYMENT_SUCCESS_CLAIM = re.compile(
    r"(?:\b(?:the|your|this|that)(?:\s+\$[0-9]+(?:\.[0-9]+)?)?\s+"
    r"(?:payment|charge|transaction|purchase)\s+"
    r"(?:went\s+through|has\s+gone\s+through|is\s+complete|was\s+completed|"
    r"succeeded|was\s+successful|was\s+approved|was\s+authorized|"
    r"was\s+submitted|was\s+sent)\b"
    r"|\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+"
    r"(?:paid|charged|submitted|sent|authorized)\s+[^.!?\n]{0,60}"
    r"\b(?:payment|charge|transaction|purchase|invoice|bill)\b)",
    re.IGNORECASE,
)
_INVOICE_SUCCESS_CLAIM = re.compile(
    r"\b(?:the|your|this|that)\s+(?:invoice|bill)\s+"
    r"(?:has\s+been\s+paid|is\s+paid|was\s+paid|is\s+complete|was\s+completed)\b",
    re.IGNORECASE,
)
_BARE_PAYMENT_SUCCESS_CLAIM = re.compile(
    r"^\s*(?:payment|transaction)\s+"
    r"(?:complete|completed|successful|succeeded)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_ON_CALENDAR = re.compile(
    r"\b(?:it(?:'s| is)|that(?:'s| is)|now)\s+on\s+(?:the|your|my|our)\s+"
    r"cal(?:endar)?\b|\bon\s+(?:the|your|my|our)\s+cal(?:endar)?\s+now\b",
    re.IGNORECASE,
)
_EXTERNAL_SERVICE_SUCCESS_CLAIM = re.compile(
    r"(?:\b(?:the|your|this|that)\s+(?:image|photo|picture|illustration|"
    r"report|research|result|research\s+job|service\s+request|task|job)\s+"
    r"(?:is|was|looks)\s+"
    r"(?:ready|generated|created|complete|completed|done|finished|successful)\b"
    r"|\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)?\s+"
    r"(?:generated|created|rendered|fetched|submitted)\s+[^.!?\n]{0,80}"
    r"\b(?:image|photo|picture|illustration|report|research|request)\b)",
    re.IGNORECASE,
)
_FIRST_PERSON_COMPLETION = re.compile(
    r"\b(?:i|we)(?:['\N{RIGHT SINGLE QUOTATION MARK}]ve| have)\s+\w+"
    r"|\b(?:i|we)\s+\w+(?:ed|en)\b",
    re.IGNORECASE,
)
_CALENDAR_REPOSITION_COMPLETION = re.compile(
    r"\b(?:back|forward|later|earlier|into|onto|off|from)\b",
    re.IGNORECASE,
)
_EXTERNAL_ACTION_SUBJECT = re.compile(
    r"\b(?:image|photo|picture|illustration|artwork|report|research|result|"
    r"service|endpoint|api\s+request|service\s+request|task|job)\b",
    re.IGNORECASE,
)
_CALENDAR_MUTATION_INTENT = re.compile(
    r"(?:\b(?:add|create|schedule|book|update|change|move|reschedule|delete|"
    r"remove|cancel|clear|put)\b[^.!?\n]{0,100}\b(?:calendar|event|"
    r"appointment|reservation|dinner|lunch|meeting|party|trip|flight|"
    r"concert|birthday|plans?)\b"
    r"|\b(?:calendar|event|appointment|reservation|dinner|lunch|meeting|"
    r"party|trip|flight|concert|birthday|plans?)\b[^.!?\n]{0,100}"
    r"\b(?:add|create|schedule|book|update|change|move|reschedule|delete|"
    r"remove|cancel|clear|put)\b)",
    re.IGNORECASE,
)
_EXTERNAL_SERVICE_INTENT = re.compile(
    r"(?:\b(?:generate|create|render|make)\b[^.!?\n]{0,80}"
    r"\b(?:image|photo|picture|illustration)\b"
    r"|\b(?:call|submit|send)\b[^.!?\n]{0,80}\b(?:service|endpoint|api|request)\b)",
    re.IGNORECASE,
)
_CLEAR_NO_ACTION_RESPONSE = re.compile(
    r"\b(?:couldn['\N{RIGHT SINGLE QUOTATION MARK}]?t|cannot|can['\N{RIGHT SINGLE QUOTATION MARK}]?t|"
    r"didn['\N{RIGHT SINGLE QUOTATION MARK}]?t|did\s+not|unable|need(?:ed)?\s+(?:you|more)|"
    r"please\s+(?:provide|clarify|choose|confirm)|which|what\s+(?:date|time))\b",
    re.IGNORECASE,
)
_READ_ONLY_OR_INFORMATIONAL_OPENING = re.compile(
    r"^\s*(?:how|why|what|when|where|who|am\s+i|is\s+(?:there|my)|"
    r"are\s+(?:there|my)|do\s+i|does|did|can\s+you\s+"
    r"(?:see|check|show|list|tell)|could\s+you\s+(?:see|check|show|list|tell)|"
    r"show|list|check|tell\s+me|explain)\b",
    re.IGNORECASE,
)
_CALENDAR_READ_INTENT = re.compile(
    r"\b(?:free|available|availability|busy|calendar|schedule|scheduled|events?|"
    r"appointments?|plans?)\b",
    re.IGNORECASE,
)
_TASK_STATUS_INTENT = re.compile(
    r"\b(?:task|job|research|service\s+request)\b[^.!?\n]{0,100}"
    r"\b(?:status|done|finish(?:ed)?|complete(?:d)?|check)\b"
    r"|\b(?:status|done|finish(?:ed)?|complete(?:d)?|check)\b[^.!?\n]{0,100}"
    r"\b(?:task|job|research|service\s+request)\b",
    re.IGNORECASE,
)


def _generic_calendar_acknowledgement(text: str) -> bool:
    normalized = re.sub(r"[^a-z]+", " ", (text or "").casefold()).strip()
    if not normalized:
        return True
    words = normalized.split()
    return len(words) <= 8 and bool(
        re.search(
            r"\b(?:done|worked|complete|completed|added|created|scheduled|updated|deleted)\b",
            normalized,
        )
    )


def claims_calendar_success(text: str) -> bool:
    """Return whether text claims that a calendar mutation already succeeded."""
    text = (text or "").strip()
    if not text:
        return False
    normalized = re.sub(r"[^a-z]+", " ", text.casefold()).strip()
    if normalized in _BARE_COMPLETION_ACKNOWLEDGEMENTS:
        return True
    if _ON_CALENDAR.search(text):
        return True
    if _STANDALONE_PUT_COMPLETION.search(text):
        return True
    if (
        _CALENDAR_SUBJECT.search(text)
        and _FIRST_PERSON_COMPLETION.search(text)
        and _CALENDAR_REPOSITION_COMPLETION.search(text)
    ):
        return True
    return bool(
        _CALENDAR_SUBJECT.search(text)
        and (
            _COMPLETED_ACTION.search(text)
            or _CALENDAR_STATE_COMPLETION.search(text)
            or _CALENDAR_PUT_COMPLETION.search(text)
            or _CALENDAR_ENTERED_COMPLETION.search(text)
            or _CALENDAR_INCLUDES_COMPLETION.search(text)
            or _CALENDAR_REMOVAL_COMPLETION.search(text)
        )
    )


def claims_unverified_side_effect_success(text: str) -> bool:
    """Return whether model text claims an executor-owned action succeeded."""
    text = (text or "").strip()
    external_first_person_claim = bool(
        _EXTERNAL_ACTION_SUBJECT.search(text) and _FIRST_PERSON_COMPLETION.search(text)
    )
    return (
        claims_calendar_success(text)
        or external_first_person_claim
        or bool(
            _PAYMENT_SUCCESS_CLAIM.search(text)
            or _INVOICE_SUCCESS_CLAIM.search(text)
            or _BARE_PAYMENT_SUCCESS_CLAIM.fullmatch(text)
            or _EXTERNAL_SERVICE_SUCCESS_CLAIM.search(text)
        )
    )


def _latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _requests_side_effect(messages: list) -> bool:
    text = _latest_user_text(messages)
    if _CALENDAR_MUTATION_INTENT.search(text) or _EXTERNAL_SERVICE_INTENT.search(text):
        return True
    return bool(
        _CALENDAR_SUBJECT.search(text)
        and len(text) <= 500
        and "?" not in text
        and not _READ_ONLY_OR_INFORMATIONAL_OPENING.search(text)
    )


def _clearly_reports_no_action_or_asks(text: str) -> bool:
    return bool(
        text.strip().casefold() == "pass"
        or "?" in text
        or _CLEAR_NO_ACTION_RESPONSE.search(text)
    )


def _verified_side_effect_success(name: str, output: str) -> bool:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or payload.get("error_code")
    ):
        return False
    expected_status = {
        "create_event": {"created", "duplicate"},
        "update_event": {"updated"},
        "delete_event": {"deleted"},
    }
    if name in expected_status:
        return payload.get("status") in expected_status[name]
    if name != "tempo_call_service":
        return False
    status = payload.get("status")
    if status is None:
        return True
    if not isinstance(status, str):
        return False
    return status.strip().casefold() in {
        "success",
        "succeeded",
        "complete",
        "completed",
        "done",
        "ready",
    }


def _successful_object_result(output: str) -> dict | None:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or payload.get("error_code")
    ):
        return None
    return payload


def _calendar_action_key(name: str, args: dict) -> tuple:
    if name == "create_event":
        return name, args.get("title", ""), args.get("start", "")
    return name, args.get("event_id", "")


def calendar_action_reply(name: str, args: dict, output: str) -> str | None:
    if name not in CALENDAR_MUTATION_TOOLS:
        return None

    title = args.get("title") or "the event"
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        result = {"error": "the calendar returned an unreadable response"}

    error = result.get("error")
    if error:
        verbs = {
            "create_event": "add",
            "update_event": "update",
            "delete_event": "delete",
        }
        if name == "create_event":
            return f"I couldn't {verbs[name]} {title} to the calendar: {error}"
        return f"I couldn't {verbs[name]} {title}: {error}"

    status = result.get("status")
    if name == "create_event" and status == "duplicate":
        existing_title = result.get("title") or title
        return f"That's already on the calendar: {existing_title}."
    if name == "create_event" and status == "created":
        return f"Done — {title} is on the calendar."
    if name == "update_event" and status == "updated":
        return f"Done — {title} was updated."
    if name == "delete_event" and status == "deleted":
        return "Done — the calendar event was deleted."

    return (
        f"I couldn't verify the calendar result for {title}, so I didn't claim "
        "it succeeded."
    )
