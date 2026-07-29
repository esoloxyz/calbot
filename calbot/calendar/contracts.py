"""Canonical calendar limits and Claude tool contracts."""

MAX_LIST_EVENTS = 50
MAX_LIST_TOTAL_EVENTS = 200
MAX_LIST_PAGES = 4
MAX_EVENT_TITLE = 200
MAX_EVENT_LOCATION = 200
MAX_EVENT_DESCRIPTION = 500

CALENDAR_FIELD_LIMITS = {
    "event_id": 1024,
    "title": MAX_EVENT_TITLE,
    "start": 100,
    "end": 100,
    "location": MAX_EVENT_LOCATION,
    "description": MAX_EVENT_DESCRIPTION,
}
CALENDAR_MUTATION_FIELDS = {
    "create_event": (
        "title",
        "start",
        "end",
        "location",
        "description",
        "all_day",
    ),
    "update_event": (
        "event_id",
        "title",
        "start",
        "end",
        "location",
        "description",
        "all_day",
    ),
    "delete_event": ("event_id",),
}
CALENDAR_REQUIRED_FIELDS = {
    "create_event": ("title", "start", "end"),
    "update_event": ("event_id",),
    "delete_event": ("event_id",),
}
CALENDAR_MUTATION_TOOLS = frozenset(CALENDAR_MUTATION_FIELDS)

CALENDAR_FIELD_SCHEMAS = {
    "event_id": {"type": "string", "maxLength": CALENDAR_FIELD_LIMITS["event_id"]},
    "title": {"type": "string", "maxLength": CALENDAR_FIELD_LIMITS["title"]},
    "start": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["start"],
        "description": (
            "RFC3339 datetime for a timed event, or the first included YYYY-MM-DD "
            "date for an all-day event."
        ),
    },
    "end": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["end"],
        "description": (
            "RFC3339 datetime, or an exclusive end date for an all-day event. "
            "Midnight after an evening event belongs to the following calendar "
            "day: 6 PM–12 AM on August 29 ends at "
            "2026-08-30T00:00:00-04:00. For an all-day range through August 10, "
            "use the exclusive end date August 11. If the user didn't specify an "
            "end, default to 1-2 hours after start."
        ),
    },
    "location": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["location"],
    },
    "description": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["description"],
    },
    "all_day": {
        "type": "boolean",
        "description": (
            "True for all-day events. Changing an existing event's type requires "
            "both start and end."
        ),
    },
}


def _mutation_input_schema(name: str) -> dict:
    return {
        "type": "object",
        "properties": {
            field_name: dict(CALENDAR_FIELD_SCHEMAS[field_name])
            for field_name in CALENDAR_MUTATION_FIELDS[name]
        },
        "required": list(CALENDAR_REQUIRED_FIELDS[name]),
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "list_events",
        "description": (
            "List events on the shared calendar between two times. "
            "Use RFC3339 timestamps with timezone offset, e.g. "
            "2026-07-10T00:00:00-04:00. Fetches bounded pages automatically and "
            "returns at most 200 events; if truncated "
            "is true, call again with next_page_token to continue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "RFC3339 start of window",
                },
                "time_max": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "RFC3339 end of window",
                },
                "page_token": {
                    "type": "string",
                    "maxLength": 2048,
                    "description": (
                        "Opaque next_page_token from a previous list_events result"
                    ),
                },
            },
            "required": ["time_min", "time_max"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_event",
        "description": (
            "Create an event on the shared calendar. The write automatically checks "
            "for an existing event with the same normalized title and overlapping "
            "time, and may return status=duplicate instead of creating another."
        ),
        "input_schema": _mutation_input_schema("create_event"),
    },
    {
        "name": "update_event",
        "description": "Update an existing event. First use list_events to find its id.",
        "input_schema": _mutation_input_schema("update_event"),
    },
    {
        "name": "delete_event",
        "description": (
            "Delete an event. First use list_events to find its id, and confirm with "
            "the user before deleting unless they were explicit."
        ),
        "input_schema": _mutation_input_schema("delete_event"),
    },
]
