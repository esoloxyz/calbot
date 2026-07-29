"""Deterministic per-message authorization for calendar tools."""

from __future__ import annotations

import re
from enum import Enum


class CalendarToolAccess(Enum):
    """Calendar capabilities authorized by the current user turn."""

    NONE = "none"
    READ = "read"
    WRITE = "write"


_WRITE_REQUEST = re.compile(
    r"(?:^|\b(?:please|also|actually|ok(?:ay)?|and\s+then|"
    r"can\s+you|could\s+you|would\s+you|"
    r"(?:i\s+)?need\s+(?:you\s+)?to|"
    r"(?:i\s+)?want\s+(?:you\s+)?to|let'?s)\s+)"
    r"(?:"
    r"add|schedule|put|create|book|block|hold|remind|set\s+up|"
    r"move|reschedule|change|update|rename|delete|remove|cancel|"
    r"postpone|shift|push|bump"
    r")\b",
    re.IGNORECASE,
)
_NEGATED_MUTATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never)\s+(?:"
    r"add|schedule|put|create|book|block|hold|remind|set\s+up|"
    r"move|reschedule|change|update|rename|delete|remove|cancel|"
    r"postpone|shift|push|bump"
    r")\b",
    re.IGNORECASE,
)
_MUTATION_STATUS_QUESTION = re.compile(
    r"(?:"
    r"\b(?:did|do|have|has)\s+you\s+"
    r"(?:add|schedule|put|create|book|move|change|update|rename|"
    r"delete|remove|cancel)\b|"
    r"\b(?:was|were|is|are)\b.+\b(?:added|scheduled|moved|changed|"
    r"updated|renamed|deleted|removed|cancelled|canceled)\b"
    r")",
    re.IGNORECASE,
)
_READ_REQUEST = re.compile(
    r"(?:"
    r"\bwhat(?:'s|\s+is|\s+do|\s+are)?\b|"
    r"\bwhen(?:'s|\s+is)?\b|"
    r"\bwhere(?:'s|\s+is)?\b|"
    r"\bwhich\b|"
    r"\b(?:show|list|check|find)\b|"
    r"\b(?:can|could|would)\s+you\s+(?:see|tell|check|find|show)\b|"
    r"\bdo\s+(?:i|we)\s+have\b|"
    r"\bare\s+(?:i|we)\s+(?:free|busy|available)\b|"
    r"\b(?:is|are)\s+there\b"
    r")",
    re.IGNORECASE,
)
_CALENDAR_CONTEXT = re.compile(
    r"\b(?:"
    r"cal|calendar|schedule|event|appointment|reservation|meeting|"
    r"reminder|plans?|free|busy|available|availability|anything"
    r")\b",
    re.IGNORECASE,
)
_READ_CONTEXT = re.compile(
    r"\b(?:"
    r"cal|calendar|schedule|plans?|free|busy|available|availability|anything"
    r")\b",
    re.IGNORECASE,
)
_TEMPORAL_DETAIL = re.compile(
    r"(?:"
    r"\b(?:today|tonight|tomorrow|weekend|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|"
    r"september|october|november|december|"
    r"morning|afternoon|evening|noon|midnight)\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)\b|"
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|"
    r"\b(?:at\s+)?(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*"
    r"(?:a\.?m\.?|p\.?m\.?)\b|"
    r"\bat\s+(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\b"
    r")",
    re.IGNORECASE,
)
_SOCIAL_TIME_PHRASE = re.compile(
    r"\b(?:see\s+you|talk\s+to\s+you|good\s+night|good\s+morning)\b",
    re.IGNORECASE,
)
_ACKNOWLEDGMENT = re.compile(
    r"^(?:thanks?|thank\s+you|good\s+stuff|perfect|great|nice|awesome|"
    r"cool|sweet|lol)\b",
    re.IGNORECASE,
)


def _current_turn_access(text: str) -> CalendarToolAccess:
    normalized = " ".join((text or "").split())
    if not normalized:
        return CalendarToolAccess.NONE
    if _NEGATED_MUTATION.search(normalized):
        return CalendarToolAccess.NONE
    if _MUTATION_STATUS_QUESTION.search(normalized):
        return CalendarToolAccess.READ
    if _WRITE_REQUEST.search(normalized):
        return CalendarToolAccess.WRITE
    if _ACKNOWLEDGMENT.search(normalized):
        return CalendarToolAccess.NONE
    has_calendar_or_time = bool(
        _CALENDAR_CONTEXT.search(normalized) or _TEMPORAL_DETAIL.search(normalized)
    )
    if (
        _READ_REQUEST.search(normalized)
        and (has_calendar_or_time or re.search(r"\bwhen\b", normalized, re.IGNORECASE))
    ) or ("?" in normalized and has_calendar_or_time):
        return CalendarToolAccess.READ
    if _READ_CONTEXT.search(normalized):
        return CalendarToolAccess.READ
    if _TEMPORAL_DETAIL.search(normalized) and not _SOCIAL_TIME_PHRASE.search(
        normalized
    ):
        # Supports terse scheduling requests such as "dinner saturday at 8."
        return CalendarToolAccess.WRITE
    return CalendarToolAccess.NONE


def calendar_tool_access(
    text: str,
    *,
    previous_user_text: str = "",
    previous_assistant_text: str = "",
) -> CalendarToolAccess:
    """Return only the calendar capability authorized by this message.

    A short answer to Calbot's immediately preceding question inherits the
    capability of the request that caused that question. Other acknowledgments
    and small talk receive no calendar tools or stale conversation history.
    """
    access = _current_turn_access(text)
    if access is not CalendarToolAccess.NONE:
        return access
    if previous_assistant_text.rstrip().endswith("?"):
        return _current_turn_access(previous_user_text)
    return CalendarToolAccess.NONE
