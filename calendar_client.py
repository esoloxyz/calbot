"""Google Calendar client + tool definitions for Claude."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarClient:
    def __init__(self):
        raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.calendar_id = os.environ["CALENDAR_ID"]
        self.timezone = os.environ.get("TIMEZONE", "America/New_York")

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
            datetime.combine(date.fromisoformat(start[:10]), time.min, timezone),
            datetime.combine(date.fromisoformat(end[:10]), time.min, timezone),
        )

    def _event_bounds(self, event: dict) -> tuple[datetime, datetime] | None:
        event_start = event.get("start", {})
        event_end = event.get("end", {})
        if event_start.get("dateTime") and event_end.get("dateTime"):
            return self._timed_bounds(
                event_start["dateTime"], event_end["dateTime"]
            )
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
        result = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
            )
            .execute()
        )
        normalized_title = self._normalize_title(title)
        for event in result.get("items", []):
            if self._normalize_title(event.get("summary", "")) != normalized_title:
                continue
            existing_bounds = self._event_bounds(event)
            if existing_bounds and self._events_overlap(
                requested_bounds, existing_bounds
            ):
                return event
        return None

    def _deterministic_event_id(
        self,
        title: str,
        start: str,
        all_day: bool,
        idempotency_key: str,
    ) -> str:
        if idempotency_key:
            fingerprint = "\0".join((self.calendar_id, idempotency_key))
        else:
            if all_day:
                canonical_start = start[:10]
            else:
                canonical_start = (
                    self._timed_bounds(start, start)[0]
                    .astimezone(timezone.utc)
                    .isoformat()
                )
            fingerprint = "\0".join(
                (self.calendar_id, self._normalize_title(title), canonical_start)
            )
        return "calbot" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32]

    def list_events(self, time_min: str, time_max: str) -> str:
        result = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
        events = []
        for e in result.get("items", []):
            events.append(
                {
                    "id": e["id"],
                    "title": e.get("summary", "(no title)"),
                    "start": e["start"].get("dateTime", e["start"].get("date")),
                    "end": e["end"].get("dateTime", e["end"].get("date")),
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                }
            )
        return json.dumps({"events": events, "count": len(events)})

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
        duplicate = self._find_duplicate_event(title, start, end, all_day)
        if duplicate:
            return self._duplicate_result(duplicate)

        if all_day:
            body_start = {"date": start[:10]}
            body_end = {"date": end[:10]}
        else:
            body_start = {"dateTime": start, "timeZone": self.timezone}
            body_end = {"dateTime": end, "timeZone": self.timezone}

        event = {
            "id": self._deterministic_event_id(
                title, start, all_day, idempotency_key
            ),
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

    def update_event(self, event_id: str, **fields) -> str:
        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )
        if fields.get("title"):
            event["summary"] = fields["title"]
        if fields.get("start"):
            event["start"] = {"dateTime": fields["start"], "timeZone": self.timezone}
        if fields.get("end"):
            event["end"] = {"dateTime": fields["end"], "timeZone": self.timezone}
        if fields.get("location") is not None and fields.get("location") != "":
            event["location"] = fields["location"]
        if fields.get("description") is not None and fields.get("description") != "":
            event["description"] = fields["description"]

        updated = (
            self.service.events()
            .update(calendarId=self.calendar_id, eventId=event_id, body=event)
            .execute()
        )
        return json.dumps({"status": "updated", "id": updated["id"]})

    def delete_event(self, event_id: str) -> str:
        self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()
        return json.dumps({"status": "deleted", "id": event_id})

    # ---- Dispatcher -------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "list_events":
                return self.list_events(args["time_min"], args["time_max"])
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
                return self.update_event(args.pop("event_id"), **args)
            if name == "delete_event":
                return self.delete_event(args["event_id"])
            return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as exc:  # surface API errors to Claude so it can explain
            return json.dumps({"error": str(exc)})


# ---- Tool schemas passed to Claude ---------------------------------------

TOOLS = [
    {
        "name": "list_events",
        "description": (
            "List events on the shared calendar between two times. "
            "Use RFC3339 timestamps with timezone offset, e.g. 2026-07-10T00:00:00-04:00."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "RFC3339 start of window"},
                "time_max": {"type": "string", "description": "RFC3339 end of window"},
            },
            "required": ["time_min", "time_max"],
        },
    },
    {
        "name": "create_event",
        "description": (
            "Create an event on the shared calendar. The write automatically checks "
            "for an existing event with the same normalized title and overlapping "
            "time, and may return status=duplicate instead of creating another."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "RFC3339 datetime, e.g. 2026-07-11T20:00:00-04:00"},
                "end": {"type": "string", "description": "RFC3339 datetime. If the user didn't specify, default to 1-2 hours after start."},
                "location": {"type": "string"},
                "description": {"type": "string"},
                "all_day": {"type": "boolean", "description": "True for all-day events (birthdays, trips)."},
            },
            "required": ["title", "start", "end"],
        },
    },
    {
        "name": "update_event",
        "description": "Update an existing event. First use list_events to find its id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete an event. First use list_events to find its id, and confirm with the user before deleting unless they were explicit.",
        "input_schema": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
]
