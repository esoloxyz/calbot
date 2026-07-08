"""Google Calendar client + tool definitions for Claude."""

import json
import os
from datetime import datetime

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
    ) -> str:
        if all_day:
            body_start = {"date": start[:10]}
            body_end = {"date": end[:10]}
        else:
            body_start = {"dateTime": start, "timeZone": self.timezone}
            body_end = {"dateTime": end, "timeZone": self.timezone}

        event = {
            "summary": title,
            "start": body_start,
            "end": body_end,
        }
        if location:
            event["location"] = location
        if description:
            event["description"] = description

        created = (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=event)
            .execute()
        )
        return json.dumps(
            {"status": "created", "id": created["id"], "link": created.get("htmlLink", "")}
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
        "description": "Create an event on the shared calendar.",
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
