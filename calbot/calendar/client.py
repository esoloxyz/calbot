"""Google Calendar client used by Calbot's model tools."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

from calbot.calendar.contracts import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    MAX_EVENT_ATTENDEES,
    MAX_EVENT_DESCRIPTION_CONTEXT,
    MAX_EVENT_LOCATION,
    MAX_EVENT_RECURRENCE_LINES,
    MAX_EVENT_REMINDERS,
    MAX_EVENT_TITLE,
    MAX_LIST_EVENTS,
    MAX_LIST_PAGES,
    MAX_LIST_TOTAL_EVENTS,
)

log = logging.getLogger("assistant-bot")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class CalendarClient:
    def __init__(
        self,
        *,
        service_account_json: str = "",
        oauth_client_id: str = "",
        oauth_client_secret: str = "",
        oauth_refresh_token: str = "",
        calendar_id: str = "",
        timezone_name: str = "",
    ):
        oauth_client_id = oauth_client_id or os.environ.get(
            "GOOGLE_OAUTH_CLIENT_ID", ""
        )
        oauth_client_secret = oauth_client_secret or os.environ.get(
            "GOOGLE_OAUTH_CLIENT_SECRET", ""
        )
        oauth_refresh_token = oauth_refresh_token or os.environ.get(
            "GOOGLE_OAUTH_REFRESH_TOKEN", ""
        )
        if all((oauth_client_id, oauth_client_secret, oauth_refresh_token)):
            creds = UserCredentials(
                token=None,
                refresh_token=oauth_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=oauth_client_id,
                client_secret=oauth_client_secret,
                scopes=SCOPES,
            )
        else:
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

    def _normalize_create_bounds(
        self,
        start: str,
        end: str,
        all_day: bool,
    ) -> tuple[str, str]:
        """Repair the common 6 PM–12 AM same-date representation."""
        if all_day or self._is_all_day_value(start) or self._is_all_day_value(end):
            return start, end

        start_value, end_value = self._timed_bounds(start, end)
        if (
            end_value <= start_value
            and end_value.date() == start_value.date()
            and end_value.hour == 0
            and end_value.minute == 0
            and end_value.second == 0
            and end_value.microsecond == 0
        ):
            end_value += timedelta(days=1)
            log.info("Moved a same-date midnight event end to the following day")
            return start, end_value.isoformat()
        return start, end

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

    @staticmethod
    def _validate_event_options(
        *,
        event_timezone: str = "",
        recurrence: list[str] | None = None,
        attendees: list[str] | None = None,
        reminder_minutes: list[int] | None = None,
        create_google_meet: bool = False,
        transparency: str = "",
        visibility: str = "",
        event_status: str = "",
        source_url: str = "",
        color_id: str = "",
        applies_to: str = "",
        send_updates: str = "none",
        recurrence_scope: str = "this_event",
    ) -> None:
        if event_timezone:
            if not isinstance(event_timezone, str) or len(event_timezone) > 100:
                raise ValueError("event_timezone is invalid")
            ZoneInfo(event_timezone)
        if recurrence is not None:
            if (
                not isinstance(recurrence, list)
                or len(recurrence) > MAX_EVENT_RECURRENCE_LINES
            ):
                raise ValueError("recurrence is invalid")
            if not all(
                isinstance(line, str)
                and len(line) <= 500
                and line.startswith(("RRULE:", "RDATE:", "EXDATE:"))
                for line in recurrence
            ):
                raise ValueError("recurrence contains invalid lines")
        if attendees is not None:
            if not isinstance(attendees, list) or len(attendees) > MAX_EVENT_ATTENDEES:
                raise ValueError("attendees is invalid")
            if not all(
                isinstance(email, str) and len(email) <= 320 and _EMAIL.fullmatch(email)
                for email in attendees
            ):
                raise ValueError("attendees contains invalid email addresses")
        if reminder_minutes is not None:
            if (
                not isinstance(reminder_minutes, list)
                or len(reminder_minutes) > MAX_EVENT_REMINDERS
                or not all(
                    type(minutes) is int and 0 <= minutes <= 40320
                    for minutes in reminder_minutes
                )
            ):
                raise ValueError("reminder_minutes contains invalid offsets")
        if type(create_google_meet) is not bool:
            raise ValueError("create_google_meet must be a boolean")
        enums = {
            "transparency": (transparency, {"", "opaque", "transparent"}),
            "visibility": (
                visibility,
                {"", "default", "public", "private", "confidential"},
            ),
            "event_status": (event_status, {"", "confirmed", "tentative"}),
            "send_updates": (send_updates, {"none", "all", "externalOnly"}),
            "recurrence_scope": (
                recurrence_scope,
                {"this_event", "entire_series"},
            ),
        }
        for field_name, (value, allowed) in enums.items():
            if value not in allowed:
                raise ValueError(f"{field_name} is invalid")
        for field_name, value in {
            "source_url": source_url,
            "color_id": color_id,
            "applies_to": applies_to,
        }.items():
            if (
                not isinstance(value, str)
                or len(value) > CALENDAR_FIELD_LIMITS[field_name]
            ):
                raise ValueError(f"{field_name} is invalid")
        if source_url:
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("source_url must be an http or https URL")

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

    def _duplicate_result(self, event: dict) -> str:
        return json.dumps({"status": "duplicate", **self._event_payload(event)})

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

    def _event_payload(self, event: dict) -> dict:
        """Return a bounded, useful event representation for the model and ledger."""
        title, title_clipped = self._clip_text(
            event.get("summary", "(no title)"), MAX_EVENT_TITLE
        )
        location, location_clipped = self._clip_text(
            event.get("location", ""), MAX_EVENT_LOCATION
        )
        description, description_clipped = self._clip_text(
            event.get("description", ""), MAX_EVENT_DESCRIPTION_CONTEXT
        )
        start = event.get("start", {})
        end = event.get("end", {})
        attendees = []
        for attendee in event.get("attendees", [])[:MAX_EVENT_ATTENDEES]:
            if not isinstance(attendee, dict):
                continue
            email, _ = self._clip_text(attendee.get("email", ""), 320)
            display_name, _ = self._clip_text(attendee.get("displayName", ""), 200)
            attendees.append(
                {
                    "email": email,
                    "display_name": display_name,
                    "response_status": attendee.get("responseStatus", ""),
                    "organizer": bool(attendee.get("organizer")),
                    "self": bool(attendee.get("self")),
                }
            )
        conference_link = event.get("hangoutLink", "")
        if not conference_link:
            for entry in event.get("conferenceData", {}).get("entryPoints", []):
                if entry.get("entryPointType") == "video":
                    conference_link = entry.get("uri", "")
                    break
        reminder_data = event.get("reminders", {})
        reminder_minutes = [
            override.get("minutes")
            for override in reminder_data.get("overrides", [])[:MAX_EVENT_REMINDERS]
            if override.get("method") == "popup"
            and type(override.get("minutes")) is int
        ]
        private_data = event.get("extendedProperties", {}).get("shared", {})
        source = event.get("source", {})
        return {
            "id": event.get("id", ""),
            "etag": event.get("etag", ""),
            "title": title,
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "all_day": "date" in start,
            "timezone": start.get("timeZone", self.timezone),
            "location": location,
            "description": description,
            "attendees": attendees,
            "reminder_minutes": reminder_minutes,
            "uses_default_reminders": bool(reminder_data.get("useDefault")),
            "conference_link": conference_link,
            "recurrence": event.get("recurrence", [])[:MAX_EVENT_RECURRENCE_LINES],
            "recurring_event_id": event.get("recurringEventId", ""),
            "original_start": event.get("originalStartTime", {}).get(
                "dateTime", event.get("originalStartTime", {}).get("date", "")
            ),
            "event_status": event.get("status", ""),
            "transparency": event.get("transparency", "opaque"),
            "visibility": event.get("visibility", "default"),
            "color_id": event.get("colorId", ""),
            "source_url": source.get("url", ""),
            "applies_to": private_data.get("calbot_applies_to", ""),
            "link": event.get("htmlLink", ""),
            "content_truncated": any(
                (title_clipped, location_clipped, description_clipped)
            ),
        }

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
                events.append(self._event_payload(event))
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
        event_timezone: str = "",
        recurrence: list[str] | None = None,
        attendees: list[str] | None = None,
        reminder_minutes: list[int] | None = None,
        create_google_meet: bool = False,
        transparency: str = "",
        visibility: str = "",
        event_status: str = "",
        source_url: str = "",
        color_id: str = "",
        applies_to: str = "",
        send_updates: str = "none",
        idempotency_key: str = "",
    ) -> str:
        start, end = self._normalize_create_bounds(start, end, all_day)
        self._validate_create_event(
            title, start, end, all_day, location=location, description=description
        )
        self._validate_event_options(
            event_timezone=event_timezone,
            recurrence=recurrence,
            attendees=attendees,
            reminder_minutes=reminder_minutes,
            create_google_meet=create_google_meet,
            transparency=transparency,
            visibility=visibility,
            event_status=event_status,
            source_url=source_url,
            color_id=color_id,
            applies_to=applies_to,
            send_updates=send_updates,
        )
        duplicate = self._find_duplicate_event(title, start, end, all_day)
        if duplicate:
            return self._duplicate_result(duplicate)

        if all_day:
            body_start = {"date": start}
            body_end = {"date": end}
        else:
            timezone_name = event_timezone or self.timezone
            body_start = {"dateTime": start, "timeZone": timezone_name}
            body_end = {"dateTime": end, "timeZone": timezone_name}

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
        if recurrence is not None:
            event["recurrence"] = recurrence
        if attendees is not None:
            event["attendees"] = [{"email": email} for email in attendees]
        if reminder_minutes is not None:
            event["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": minutes}
                    for minutes in reminder_minutes
                ],
            }
        if transparency:
            event["transparency"] = transparency
        if visibility:
            event["visibility"] = visibility
        if event_status:
            event["status"] = event_status
        if source_url:
            event["source"] = {"title": "Calbot source", "url": source_url}
        if color_id:
            event["colorId"] = color_id
        if applies_to:
            event["extendedProperties"] = {"shared": {"calbot_applies_to": applies_to}}
        if create_google_meet:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": hashlib.sha256(event["id"].encode()).hexdigest()[:32],
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        try:
            insert_args = {
                "calendarId": self.calendar_id,
                "body": event,
                "sendUpdates": send_updates,
            }
            if create_google_meet:
                insert_args["conferenceDataVersion"] = 1
            created = self.service.events().insert(**insert_args).execute()
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
        payload = self._event_payload(created)
        payload.update(
            {
                "id": created.get("id", event["id"]),
                "title": created.get("summary", title),
                "start": payload.get("start") or start,
                "end": payload.get("end") or end,
                "all_day": all_day,
            }
        )
        return json.dumps({"status": "created", **payload})

    def preview_mutation(self, name: str, args: dict) -> dict:
        """Return user-reviewable data without performing a calendar write."""
        if name == "create_event":
            start, end = self._normalize_create_bounds(
                args["start"],
                args["end"],
                args.get("all_day", False),
            )
            self._validate_create_event(
                args["title"],
                start,
                end,
                args.get("all_day", False),
                location=args.get("location", ""),
                description=args.get("description", ""),
            )
            self._validate_event_options(
                event_timezone=args.get("event_timezone", ""),
                recurrence=args.get("recurrence"),
                attendees=args.get("attendees"),
                reminder_minutes=args.get("reminder_minutes"),
                create_google_meet=args.get("create_google_meet", False),
                transparency=args.get("transparency", ""),
                visibility=args.get("visibility", ""),
                event_status=args.get("event_status", ""),
                source_url=args.get("source_url", ""),
                color_id=args.get("color_id", ""),
                applies_to=args.get("applies_to", ""),
                send_updates=args.get("send_updates", "none"),
            )
            normalized = dict(args)
            normalized.update({"start": start, "end": end})
            return {
                "action": name,
                "event": {
                    key: value
                    for key, value in normalized.items()
                    if not key.startswith("_")
                },
            }
        if name not in {"update_event", "delete_event"}:
            raise ValueError(f"Unsupported calendar mutation: {name}")

        self._validate_event_options(
            event_timezone=args.get("event_timezone", ""),
            recurrence=args.get("recurrence"),
            attendees=args.get("attendees"),
            reminder_minutes=args.get("reminder_minutes"),
            create_google_meet=args.get("create_google_meet", False),
            transparency=args.get("transparency", ""),
            visibility=args.get("visibility", ""),
            event_status=args.get("event_status", ""),
            source_url=args.get("source_url", ""),
            color_id=args.get("color_id", ""),
            applies_to=args.get("applies_to", ""),
            send_updates=args.get("send_updates", "none"),
            recurrence_scope=args.get("recurrence_scope", "this_event"),
        )
        event_id = args["event_id"]
        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )
        resolved_event_id = event_id
        if args.get("recurrence_scope") == "entire_series" and event.get(
            "recurringEventId"
        ):
            resolved_event_id = event["recurringEventId"]
            event = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=resolved_event_id)
                .execute()
            )
        current_event = self._event_payload(event)
        current_event["id"] = resolved_event_id
        event_etag = event.get("etag")
        if not isinstance(event_etag, str) or not event_etag:
            raise ValueError("Google Calendar did not return an event version")
        preview = {
            "action": name,
            "current_event": current_event,
            "event_etag": event_etag,
            "resolved_event_id": resolved_event_id,
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

        if (
            new_end <= new_start
            and isinstance(new_start, datetime)
            and isinstance(new_end, datetime)
            and new_end.date() == new_start.date()
            and new_end.hour == 0
            and new_end.minute == 0
            and new_end.second == 0
            and new_end.microsecond == 0
        ):
            new_end += timedelta(days=1)
            log.info("Moved a same-date midnight event update to the following day")

        if new_end <= new_start:
            raise ValueError("event end must be after start")

        if new_all_day:
            return {"date": new_start.isoformat()}, {"date": new_end.isoformat()}
        timezone_name = fields.get("event_timezone") or old_start.get(
            "timeZone", self.timezone
        )
        return (
            {"dateTime": new_start.isoformat(), "timeZone": timezone_name},
            {"dateTime": new_end.isoformat(), "timeZone": timezone_name},
        )

    def update_event(self, event_id: str, *, expected_etag: str = "", **fields) -> str:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if len(event_id) > CALENDAR_FIELD_LIMITS["event_id"]:
            raise ValueError("event_id is too long")
        unsupported = set(fields) - set(CALENDAR_MUTATION_FIELDS["update_event"])
        if unsupported:
            raise ValueError("calendar update has unsupported fields")
        send_updates = fields.pop("send_updates", "none")
        recurrence_scope = fields.pop("recurrence_scope", "this_event")
        self._validate_event_options(
            event_timezone=fields.get("event_timezone", ""),
            recurrence=fields.get("recurrence"),
            attendees=fields.get("attendees"),
            reminder_minutes=fields.get("reminder_minutes"),
            create_google_meet=fields.get("create_google_meet", False),
            transparency=fields.get("transparency", ""),
            visibility=fields.get("visibility", ""),
            event_status=fields.get("event_status", ""),
            source_url=fields.get("source_url", ""),
            color_id=fields.get("color_id", ""),
            applies_to=fields.get("applies_to", ""),
            send_updates=send_updates,
            recurrence_scope=recurrence_scope,
        )
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
                        "The event changed while I was working on it. Please ask me "
                        "to update it again."
                    ),
                    "error_code": "event_changed_before_write",
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

        simple_mappings = {
            "transparency": "transparency",
            "visibility": "visibility",
            "event_status": "status",
            "color_id": "colorId",
        }
        for field_name, google_name in simple_mappings.items():
            if field_name not in fields:
                continue
            if fields[field_name] == "":
                event.pop(google_name, None)
            else:
                event[google_name] = fields[field_name]
        if "recurrence" in fields:
            if fields["recurrence"]:
                event["recurrence"] = list(fields["recurrence"])
            else:
                event.pop("recurrence", None)
        if "attendees" in fields:
            event["attendees"] = [{"email": email} for email in fields["attendees"]]
        if "reminder_minutes" in fields:
            event["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": minutes}
                    for minutes in fields["reminder_minutes"]
                ],
            }
        if "source_url" in fields:
            if fields["source_url"]:
                event["source"] = {
                    "title": "Calbot source",
                    "url": fields["source_url"],
                }
            else:
                event.pop("source", None)
        if "applies_to" in fields:
            extended = event.setdefault("extendedProperties", {})
            shared = extended.setdefault("shared", {})
            if fields["applies_to"]:
                shared["calbot_applies_to"] = fields["applies_to"]
            else:
                shared.pop("calbot_applies_to", None)
        create_google_meet = fields.get("create_google_meet", False)
        if create_google_meet:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": hashlib.sha256(
                        f"{event_id}:{expected_etag}".encode()
                    ).hexdigest()[:32],
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        update_args = {
            "calendarId": self.calendar_id,
            "eventId": event_id,
            "body": event,
            "sendUpdates": send_updates,
        }
        if create_google_meet:
            update_args["conferenceDataVersion"] = 1
        request = self.service.events().update(**update_args)
        if expected_etag and hasattr(request, "headers"):
            request.headers["If-Match"] = expected_etag
        updated = request.execute()
        return json.dumps({"status": "updated", **self._event_payload(updated)})

    def delete_event(
        self,
        event_id: str,
        *,
        expected_etag: str = "",
        send_updates: str = "none",
    ) -> str:
        self._validate_event_options(send_updates=send_updates)
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
                            "The event changed while I was working on it. Please ask "
                            "me to delete it again."
                        ),
                        "error_code": "event_changed_before_write",
                    }
                )
        request = self.service.events().delete(
            calendarId=self.calendar_id,
            eventId=event_id,
            sendUpdates=send_updates,
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
                options = {
                    key: value
                    for key, value in args.items()
                    if key
                    in {
                        "event_timezone",
                        "recurrence",
                        "attendees",
                        "reminder_minutes",
                        "create_google_meet",
                        "transparency",
                        "visibility",
                        "event_status",
                        "source_url",
                        "color_id",
                        "applies_to",
                        "send_updates",
                    }
                }
                return self.create_event(
                    title=args["title"],
                    start=args["start"],
                    end=args["end"],
                    location=args.get("location", ""),
                    description=args.get("description", ""),
                    all_day=args.get("all_day", False),
                    idempotency_key=args.get("_idempotency_key", ""),
                    **options,
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
                    send_updates=args.get("send_updates", "none"),
                )
            return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as exc:  # Return API failures to the bounded caller.
            return json.dumps({"error": str(exc)})
