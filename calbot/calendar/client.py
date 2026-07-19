"""Google Calendar client + tool definitions for Claude."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
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
CALENDAR_FIELD_SCHEMAS = {
    "event_id": {"type": "string", "maxLength": CALENDAR_FIELD_LIMITS["event_id"]},
    "title": {"type": "string", "maxLength": CALENDAR_FIELD_LIMITS["title"]},
    "start": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["start"],
        "description": "RFC3339 datetime, e.g. 2026-07-11T20:00:00-04:00",
    },
    "end": {
        "type": "string",
        "maxLength": CALENDAR_FIELD_LIMITS["end"],
        "description": (
            "RFC3339 datetime. If the user didn't specify, default to 1-2 hours "
            "after start."
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


class CalendarClient:
    def __init__(
        self,
        *,
        service_account_json: str = "",
        calendar_id: str = "",
        timezone_name: str = "",
    ):
        raw = service_account_json or os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
        authorized_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
        self.service = build(
            "calendar",
            "v3",
            http=authorized_http,
            cache_discovery=False,
        )
        self.calendar_id = calendar_id or os.environ["CALENDAR_ID"]
        self.timezone = timezone_name or os.environ.get("TIMEZONE", "America/New_York")

    # ---- Tool implementations -------------------------------------------

    @staticmethod
    def _normalize_title(title: str) -> str:
        normalized = unicodedata.normalize("NFKC", title or "").casefold()
        normalized = normalized.replace("&", " and ")
        return re.sub(r"[^\w]+", "", normalized)

    def _timed_bounds(self, start: str, end: str) -> tuple[datetime, datetime]:
        timezone = ZoneInfo(self.timezone)

        def parse(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)

        return parse(start), parse(end)

    def _all_day_bounds(self, start: str, end: str) -> tuple[datetime, datetime]:
        timezone = ZoneInfo(self.timezone)
        return (
            datetime.combine(date.fromisoformat(start), time.min, timezone),
            datetime.combine(date.fromisoformat(end), time.min, timezone),
        )

    def _validate_create_event(
        self,
        title: str,
        start: str,
        end: str,
        all_day: bool,
        location: str = "",
        description: str = "",
    ) -> None:
        string_fields = {
            "title": title,
            "start": start,
            "end": end,
            "location": location,
            "description": description,
        }
        if not all(isinstance(value, str) for value in string_fields.values()):
            raise ValueError("event text fields must be strings")
        for field_name, value in string_fields.items():
            if len(value) > CALENDAR_FIELD_LIMITS[field_name]:
                raise ValueError(f"event {field_name} is too long")
        if not title.strip():
            raise ValueError("event title cannot be empty")
        if type(all_day) is not bool:
            raise ValueError("all_day must be a boolean")
        if all_day:
            if not self._is_all_day_value(start) or not self._is_all_day_value(end):
                raise ValueError("all-day event boundaries must be dates")
            bounds = self._all_day_bounds(start, end)
        else:
            if self._is_all_day_value(start) or self._is_all_day_value(end):
                raise ValueError("timed event boundaries must be RFC3339 date-times")
            bounds = self._timed_bounds(start, end)
        if bounds[1] <= bounds[0]:
            raise ValueError("event end must be after start")

    def _event_bounds(self, event: dict) -> tuple[datetime, datetime] | None:
        event_start = event.get("start", {})
        event_end = event.get("end", {})
        if event_start.get("dateTime") and event_end.get("dateTime"):
            return self._timed_bounds(event_start["dateTime"], event_end["dateTime"])
        if event_start.get("date") and event_end.get("date"):
            return self._all_day_bounds(event_start["date"], event_end["date"])
        return None

    @staticmethod
    def _events_overlap(
        first: tuple[datetime, datetime], second: tuple[datetime, datetime]
    ) -> bool:
        return first[0] < second[1] and second[0] < first[1]

    @staticmethod
    def _duplicate_result(event: dict) -> str:
        return json.dumps(
            {
                "status": "duplicate",
                "id": event.get("id", ""),
                "title": event.get("summary", "(no title)"),
                "start": event.get("start", {}).get(
                    "dateTime", event.get("start", {}).get("date", "")
                ),
                "link": event.get("htmlLink", ""),
            }
        )

    def _find_duplicate_event(
        self,
        title: str,
        start: str,
        end: str,
        all_day: bool,
    ) -> dict | None:
        requested_bounds = (
            self._all_day_bounds(start, end)
            if all_day
            else self._timed_bounds(start, end)
        )
        # A bounded query keeps duplicate prevention cheap while still finding events
        # that begin shortly before the requested event and overlap it.
        time_min = (requested_bounds[0] - timedelta(days=1)).isoformat()
        time_max = (requested_bounds[1] + timedelta(days=1)).isoformat()
        normalized_title = self._normalize_title(title)
        for page in self._iter_event_pages(time_min, time_max):
            for event in page:
                if self._normalize_title(event.get("summary", "")) != normalized_title:
                    continue
                existing_bounds = self._event_bounds(event)
                if existing_bounds and self._events_overlap(
                    requested_bounds, existing_bounds
                ):
                    return event
        return None

    def _iter_event_pages(
        self,
        time_min: str,
        time_max: str,
        *,
        max_results: int = 2500,
    ):
        page_token = None
        seen_tokens = set()
        while True:
            kwargs = {
                "calendarId": self.calendar_id,
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": max_results,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            result = self.service.events().list(**kwargs).execute()
            yield result.get("items", [])
            next_token = result.get("nextPageToken")
            if not next_token:
                return
            if next_token in seen_tokens:
                raise RuntimeError("Google Calendar returned a repeated page token")
            seen_tokens.add(next_token)
            page_token = next_token

    def _deterministic_event_id(
        self,
        title: str,
        start: str,
        all_day: bool,
        idempotency_key: str,
    ) -> str:
        if all_day:
            canonical_start = start
        else:
            canonical_start = (
                self._timed_bounds(start, start)[0].astimezone(timezone.utc).isoformat()
            )
        fingerprint = "\0".join(
            (
                self.calendar_id,
                idempotency_key,
                "all-day" if all_day else "timed",
                self._normalize_title(title),
                canonical_start,
            )
        )
        return "calbot" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32]

    @staticmethod
    def _clip_text(value, limit: int) -> tuple[str, bool]:
        text = str(value or "")
        if len(text) <= limit:
            return text, False
        return text[: limit - 1] + "…", True

    def list_events(self, time_min: str, time_max: str, page_token: str = "") -> str:
        if not all(isinstance(value, str) for value in (time_min, time_max)):
            raise ValueError("calendar time bounds must be strings")
        if not time_min or not time_max or max(len(time_min), len(time_max)) > 100:
            raise ValueError("calendar time bounds must be RFC3339 values")
        try:
            raw_start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
            raw_end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("calendar time bounds must be RFC3339 values") from exc
        if raw_start.tzinfo is None or raw_end.tzinfo is None:
            raise ValueError("calendar time bounds require a timezone offset")
        start, end = self._timed_bounds(time_min, time_max)
        if end <= start:
            raise ValueError("calendar time_max must be after time_min")
        if not isinstance(page_token, str) or len(page_token) > 2048:
            raise ValueError("page_token must be a string of at most 2048 characters")
        events = []
        next_page_token = page_token
        seen_tokens = {page_token}
        truncated = False
        for _ in range(MAX_LIST_PAGES):
            kwargs = {
                "calendarId": self.calendar_id,
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": MAX_LIST_EVENTS,
            }
            if next_page_token:
                kwargs["pageToken"] = next_page_token
            result = self.service.events().list(**kwargs).execute()
            items = result.get("items", [])
            if not isinstance(items, list):
                raise ValueError("Google Calendar returned invalid events")
            remaining = MAX_LIST_TOTAL_EVENTS - len(events)
            for event in items[: min(MAX_LIST_EVENTS, remaining)]:
                title, title_clipped = self._clip_text(
                    event.get("summary", "(no title)"), MAX_EVENT_TITLE
                )
                location, location_clipped = self._clip_text(
                    event.get("location", ""), MAX_EVENT_LOCATION
                )
                description, description_clipped = self._clip_text(
                    event.get("description", ""), MAX_EVENT_DESCRIPTION
                )
                events.append(
                    {
                        "id": event["id"],
                        "title": title,
                        "start": event["start"].get(
                            "dateTime", event["start"].get("date")
                        ),
                        "end": event["end"].get("dateTime", event["end"].get("date")),
                        "location": location,
                        "description": description,
                        "content_truncated": any(
                            (title_clipped, location_clipped, description_clipped)
                        ),
                    }
                )
            next_token = result.get("nextPageToken", "")
            if not isinstance(next_token, str) or len(next_token) > 2048:
                raise ValueError("Google Calendar returned an invalid page token")
            if len(items) > remaining:
                truncated = True
                break
            if not next_token:
                next_page_token = ""
                break
            if next_token in seen_tokens:
                truncated = True
                next_page_token = next_token
                break
            next_page_token = next_token
            seen_tokens.add(next_token)
            if len(events) >= MAX_LIST_TOTAL_EVENTS:
                truncated = True
                break
        else:
            truncated = bool(next_page_token)
        truncated = truncated or bool(next_page_token)
        return json.dumps(
            {
                "events": events,
                "count": len(events),
                "truncated": truncated,
                "next_page_token": next_page_token,
                "notice": (
                    "More calendar events exist outside this bounded result. "
                    "Do not describe it as complete; continue with next_page_token."
                    if truncated
                    else ""
                ),
            }
        )

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
        all_day: bool = False,
        idempotency_key: str = "",
    ) -> str:
        self._validate_create_event(
            title, start, end, all_day, location=location, description=description
        )
        duplicate = self._find_duplicate_event(title, start, end, all_day)
        if duplicate:
            return self._duplicate_result(duplicate)

        if all_day:
            body_start = {"date": start}
            body_end = {"date": end}
        else:
            body_start = {"dateTime": start, "timeZone": self.timezone}
            body_end = {"dateTime": end, "timeZone": self.timezone}

        event = {
            "id": self._deterministic_event_id(title, start, all_day, idempotency_key),
            "summary": title,
            "start": body_start,
            "end": body_end,
        }
        if location:
            event["location"] = location
        if description:
            event["description"] = description

        try:
            created = (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=event)
                .execute()
            )
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) != 409:
                raise
            # Another request created the same deterministic event after our preflight
            # check. Resolve the write race as an existing event, not an error or retry.
            created = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=event["id"])
                .execute()
            )
            return self._duplicate_result(created)
        return json.dumps(
            {
                "status": "created",
                "id": created["id"],
                "title": title,
                "start": start,
                "link": created.get("htmlLink", ""),
            }
        )

    def preview_mutation(self, name: str, args: dict) -> dict:
        """Return user-reviewable data without performing a calendar write."""
        if name == "create_event":
            self._validate_create_event(
                args["title"],
                args["start"],
                args["end"],
                args.get("all_day", False),
                location=args.get("location", ""),
                description=args.get("description", ""),
            )
            return {
                "action": name,
                "event": {
                    key: value for key, value in args.items() if not key.startswith("_")
                },
            }
        if name not in {"update_event", "delete_event"}:
            raise ValueError(f"Unsupported calendar mutation: {name}")

        event_id = args["event_id"]
        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )
        current_event = {
            "id": event_id,
            "title": event.get("summary", "(no title)"),
            "start": event.get("start", {}).get(
                "dateTime", event.get("start", {}).get("date", "")
            ),
            "end": event.get("end", {}).get(
                "dateTime", event.get("end", {}).get("date", "")
            ),
        }
        event_etag = event.get("etag")
        if not isinstance(event_etag, str) or not event_etag:
            raise ValueError("Google Calendar did not return an event version")
        preview = {
            "action": name,
            "current_event": current_event,
            "event_etag": event_etag,
        }
        if name == "update_event":
            preview["changes"] = {
                key: value for key, value in args.items() if key != "event_id"
            }
        return preview

    @staticmethod
    def _is_all_day_value(value: str) -> bool:
        return "T" not in value

    def _parse_update_boundary(self, value: str, all_day: bool):
        if all_day:
            if not self._is_all_day_value(value):
                raise ValueError("all-day event boundaries must be dates")
            return date.fromisoformat(value)
        if self._is_all_day_value(value):
            raise ValueError("timed event boundaries must be RFC3339 date-times")
        return self._timed_bounds(value, value)[0]

    def _updated_temporal_fields(self, event: dict, fields: dict) -> tuple[dict, dict]:
        old_start = event.get("start", {})
        old_end = event.get("end", {})
        old_all_day = "date" in old_start and "date" in old_end
        old_timed = "dateTime" in old_start and "dateTime" in old_end
        if not old_all_day and not old_timed:
            raise ValueError("existing event has invalid start/end fields")

        has_start = "start" in fields
        has_end = "end" in fields
        requested_kind = fields.get("all_day")
        if requested_kind is not None and type(requested_kind) is not bool:
            raise ValueError("all_day must be a boolean")
        supplied_kinds = {
            self._is_all_day_value(str(fields[key]))
            for key in ("start", "end")
            if key in fields
        }
        if len(supplied_kinds) > 1:
            raise ValueError("start and end must both be dates or both be date-times")
        if requested_kind is None:
            new_all_day = next(iter(supplied_kinds), old_all_day)
        else:
            new_all_day = bool(requested_kind)
            if supplied_kinds and next(iter(supplied_kinds)) != new_all_day:
                raise ValueError("all_day does not match the supplied start/end values")

        if new_all_day != old_all_day and not (has_start and has_end):
            raise ValueError("changing event type requires both start and end")

        old_start_value = old_start["date" if old_all_day else "dateTime"]
        old_end_value = old_end["date" if old_all_day else "dateTime"]
        old_start_parsed = self._parse_update_boundary(old_start_value, old_all_day)
        old_end_parsed = self._parse_update_boundary(old_end_value, old_all_day)
        duration = old_end_parsed - old_start_parsed

        if has_start:
            new_start = self._parse_update_boundary(str(fields["start"]), new_all_day)
        elif new_all_day == old_all_day:
            new_start = old_start_parsed
        else:
            raise ValueError("changing event type requires both start and end")

        if has_end:
            new_end = self._parse_update_boundary(str(fields["end"]), new_all_day)
        elif has_start:
            new_end = new_start + duration
        elif new_all_day == old_all_day:
            new_end = old_end_parsed
        else:
            raise ValueError("changing event type requires both start and end")

        if new_end <= new_start:
            raise ValueError("event end must be after start")

        if new_all_day:
            return {"date": new_start.isoformat()}, {"date": new_end.isoformat()}
        return (
            {"dateTime": new_start.isoformat(), "timeZone": self.timezone},
            {"dateTime": new_end.isoformat(), "timeZone": self.timezone},
        )

    def update_event(self, event_id: str, *, expected_etag: str = "", **fields) -> str:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if len(event_id) > CALENDAR_FIELD_LIMITS["event_id"]:
            raise ValueError("event_id is too long")
        unsupported = set(fields) - set(CALENDAR_MUTATION_FIELDS["update_event"])
        if unsupported:
            raise ValueError("calendar update has unsupported fields")
        for field_name in set(fields) & set(CALENDAR_FIELD_LIMITS):
            value = fields[field_name]
            if not isinstance(value, str):
                raise ValueError(f"event {field_name} must be a string")
            if len(value) > CALENDAR_FIELD_LIMITS[field_name]:
                raise ValueError(f"event {field_name} is too long")
        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )
        if expected_etag and event.get("etag") != expected_etag:
            return json.dumps(
                {
                    "error": (
                        "The event changed after it was previewed. Review it again "
                        "before approving an update."
                    ),
                    "error_code": "event_changed_since_approval",
                }
            )
        if "title" in fields:
            event["summary"] = fields["title"]
        if "start" in fields or "end" in fields or "all_day" in fields:
            try:
                event["start"], event["end"] = self._updated_temporal_fields(
                    event, fields
                )
            except (KeyError, TypeError, ValueError) as exc:
                return json.dumps({"error": str(exc)})
        for field in ("location", "description"):
            if field not in fields:
                continue
            if fields[field] == "":
                event.pop(field, None)
            else:
                event[field] = fields[field]

        request = self.service.events().update(
            calendarId=self.calendar_id, eventId=event_id, body=event
        )
        if expected_etag and hasattr(request, "headers"):
            request.headers["If-Match"] = expected_etag
        updated = request.execute()
        return json.dumps({"status": "updated", "id": updated["id"]})

    def delete_event(self, event_id: str, *, expected_etag: str = "") -> str:
        if expected_etag:
            event = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
            if event.get("etag") != expected_etag:
                return json.dumps(
                    {
                        "error": (
                            "The event changed after it was previewed. Review it "
                            "again before approving deletion."
                        ),
                        "error_code": "event_changed_since_approval",
                    }
                )
        request = self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        )
        if expected_etag and hasattr(request, "headers"):
            request.headers["If-Match"] = expected_etag
        request.execute()
        return json.dumps({"status": "deleted", "id": event_id})

    # ---- Dispatcher -------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "list_events":
                return self.list_events(
                    args["time_min"],
                    args["time_max"],
                    page_token=args.get("page_token", ""),
                )
            if name == "create_event":
                return self.create_event(
                    title=args["title"],
                    start=args["start"],
                    end=args["end"],
                    location=args.get("location", ""),
                    description=args.get("description", ""),
                    all_day=args.get("all_day", False),
                    idempotency_key=args.get("_idempotency_key", ""),
                )
            if name == "update_event":
                event_id = args["event_id"]
                fields = {
                    key: value
                    for key, value in args.items()
                    if key != "event_id" and not key.startswith("_")
                }
                return self.update_event(
                    event_id,
                    expected_etag=args.get("_expected_etag", ""),
                    **fields,
                )
            if name == "delete_event":
                return self.delete_event(
                    args["event_id"],
                    expected_etag=args.get("_expected_etag", ""),
                )
            return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as exc:  # surface API errors to Claude so it can explain
            return json.dumps({"error": str(exc)})


# ---- Tool schemas passed to Claude ---------------------------------------

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
                    "description": "Opaque next_page_token from a previous list_events result",
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
        "description": "Delete an event. First use list_events to find its id, and confirm with the user before deleting unless they were explicit.",
        "input_schema": _mutation_input_schema("delete_event"),
    },
]
