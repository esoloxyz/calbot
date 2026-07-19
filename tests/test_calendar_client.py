import json
import sys
import types
import unittest
from types import SimpleNamespace

# The repository's lightweight local test runner may not have Google's SDK installed.
# CalendarClient.__init__ is bypassed in these unit tests, so small import stubs are enough.
try:
    import google.oauth2.service_account  # noqa: F401
    import googleapiclient.discovery  # noqa: F401
except ModuleNotFoundError:
    google = types.ModuleType("google")
    google_oauth2 = types.ModuleType("google.oauth2")
    google_service_account = types.ModuleType("google.oauth2.service_account")
    google_service_account.Credentials = object
    googleapiclient = types.ModuleType("googleapiclient")
    google_discovery = types.ModuleType("googleapiclient.discovery")
    google_discovery.build = lambda *args, **kwargs: None
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.oauth2", google_oauth2)
    sys.modules.setdefault("google.oauth2.service_account", google_service_account)
    sys.modules.setdefault("googleapiclient", googleapiclient)
    sys.modules.setdefault("googleapiclient.discovery", google_discovery)

from calbot.calendar.client import (
    CALENDAR_FIELD_LIMITS,
    CALENDAR_MUTATION_FIELDS,
    CALENDAR_REQUIRED_FIELDS,
    SCOPES,
    TOOLS,
    CalendarClient,
)


class FakeRequest:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.headers = {}

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeEventsApi:
    def __init__(
        self,
        listed=None,
        created=None,
        insert_error=None,
        fetched=None,
        pages=None,
    ):
        self.listed = listed or []
        self.created = created or {"id": "new-event", "htmlLink": "https://event/new"}
        self.insert_error = insert_error
        self.fetched = fetched
        self.pages = pages
        self.list_calls = []
        self.insert_calls = []
        self.get_calls = []
        self.update_calls = []
        self.delete_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.pages is not None:
            return FakeRequest(self.pages[kwargs.get("pageToken")])
        return FakeRequest({"items": self.listed})

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return FakeRequest(self.created, self.insert_error)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.fetched)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return FakeRequest({"id": kwargs["eventId"], **kwargs["body"]})

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeRequest({})


class FakeService:
    def __init__(self, events_api):
        self.events_api = events_api

    def events(self):
        return self.events_api


def calendar_with(events_api):
    calendar = CalendarClient.__new__(CalendarClient)
    calendar.service = FakeService(events_api)
    calendar.calendar_id = "shared@example.com"
    calendar.timezone = "America/New_York"
    return calendar


class ConflictError(Exception):
    def __init__(self):
        super().__init__("409 Conflict")
        self.resp = SimpleNamespace(status=409)


class CalendarCreateDeduplicationTests(unittest.TestCase):
    def test_invalid_create_fields_are_rejected_before_calendar_api_calls(self):
        cases = (
            {
                "title": "Dinner",
                "start": "2026-07-19T19:00:00-04:00",
                "end": "2026-07-19T21:00:00-04:00",
                "all_day": "false",
            },
            {
                "title": "Dinner",
                "start": "2026-07-19T21:00:00-04:00",
                "end": "2026-07-19T19:00:00-04:00",
            },
            {
                "title": "Dinner",
                "start": "2026-07-19T19:00:00-04:00",
                "end": "2026-07-19T21:00:00-04:00",
                "description": "x" * (CALENDAR_FIELD_LIMITS["description"] + 1),
            },
        )
        for args in cases:
            with self.subTest(args=args):
                api = FakeEventsApi()

                result = json.loads(calendar_with(api).run_tool("create_event", args))

                self.assertIn("error", result)
                self.assertEqual(api.list_calls, [])
                self.assertEqual(api.insert_calls, [])

    def test_mutation_schemas_derive_from_one_canonical_contract(self):
        schemas = {tool["name"]: tool["input_schema"] for tool in TOOLS}

        for tool_name, fields in CALENDAR_MUTATION_FIELDS.items():
            with self.subTest(tool_name=tool_name):
                schema = schemas[tool_name]
                self.assertEqual(tuple(schema["properties"]), fields)
                self.assertEqual(
                    tuple(schema["required"]), CALENDAR_REQUIRED_FIELDS[tool_name]
                )
                self.assertFalse(schema["additionalProperties"])
        self.assertEqual(SCOPES, ["https://www.googleapis.com/auth/calendar.events"])

    def test_same_normalized_title_and_overlapping_time_is_a_duplicate(self):
        api = FakeEventsApi(
            listed=[
                {
                    "id": "existing-event",
                    "summary": "  BARI & JOHN'S WEDDING! ",
                    "start": {"dateTime": "2026-11-20T19:00:00-05:00"},
                    "end": {"dateTime": "2026-11-20T23:00:00-05:00"},
                    "htmlLink": "https://event/existing",
                }
            ]
        )

        result = json.loads(
            calendar_with(api).create_event(
                title="Bari and Johns Wedding",
                start="2026-11-20T19:00:00-05:00",
                end="2026-11-20T23:00:00-05:00",
            )
        )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["id"], "existing-event")
        self.assertEqual(len(api.list_calls), 1)
        self.assertEqual(api.list_calls[0]["maxResults"], 2500)
        self.assertEqual(api.insert_calls, [])

    def test_same_title_at_a_non_overlapping_time_is_created(self):
        api = FakeEventsApi(
            listed=[
                {
                    "id": "earlier-event",
                    "summary": "Dinner",
                    "start": {"dateTime": "2026-07-19T17:00:00-04:00"},
                    "end": {"dateTime": "2026-07-19T18:00:00-04:00"},
                }
            ]
        )

        result = json.loads(
            calendar_with(api).create_event(
                title="Dinner",
                start="2026-07-19T19:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
            )
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(len(api.list_calls), 1)
        self.assertEqual(len(api.insert_calls), 1)

    def test_different_title_at_the_same_time_is_created(self):
        api = FakeEventsApi(
            listed=[
                {
                    "id": "other-event",
                    "summary": "Dentist",
                    "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                    "end": {"dateTime": "2026-07-19T20:00:00-04:00"},
                }
            ]
        )

        result = json.loads(
            calendar_with(api).create_event(
                title="Dinner",
                start="2026-07-19T19:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
            )
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(len(api.insert_calls), 1)

    def test_all_day_duplicate_is_not_created(self):
        api = FakeEventsApi(
            listed=[
                {
                    "id": "birthday",
                    "summary": "Sarah's Birthday",
                    "start": {"date": "2026-09-12"},
                    "end": {"date": "2026-09-13"},
                }
            ]
        )

        result = json.loads(
            calendar_with(api).create_event(
                title="Sarahs Birthday",
                start="2026-09-12",
                end="2026-09-13",
                all_day=True,
            )
        )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(api.insert_calls, [])

    def test_same_idempotency_key_uses_the_same_event_id(self):
        first_api = FakeEventsApi()
        second_api = FakeEventsApi()

        first = calendar_with(first_api)
        second = calendar_with(second_api)
        for calendar in (first, second):
            calendar.create_event(
                title="Dinner",
                start="2026-07-19T19:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
                idempotency_key="telegram:-100:42",
            )

        first_id = first_api.insert_calls[0]["body"]["id"]
        second_id = second_api.insert_calls[0]["body"]["id"]
        self.assertEqual(first_id, second_id)
        self.assertRegex(first_id, r"^calbot[0-9a-f]{32}$")

    def test_one_message_can_create_multiple_distinct_event_ids(self):
        first_api = FakeEventsApi()
        second_api = FakeEventsApi()

        calendar_with(first_api).create_event(
            title="Dinner",
            start="2026-07-19T19:00:00-04:00",
            end="2026-07-19T21:00:00-04:00",
            idempotency_key="telegram:-100:42",
        )
        calendar_with(second_api).create_event(
            title="Dentist",
            start="2026-07-20T09:00:00-04:00",
            end="2026-07-20T10:00:00-04:00",
            idempotency_key="telegram:-100:42",
        )

        self.assertNotEqual(
            first_api.insert_calls[0]["body"]["id"],
            second_api.insert_calls[0]["body"]["id"],
        )

    def test_new_message_can_recreate_a_previously_deleted_event(self):
        first_api = FakeEventsApi()
        second_api = FakeEventsApi()

        calendar_with(first_api).create_event(
            title="Dinner",
            start="2026-07-19T19:00:00-04:00",
            end="2026-07-19T21:00:00-04:00",
            idempotency_key="telegram:-100:42",
        )
        # A later Telegram message gets a new key. If the original event was deleted,
        # the preflight returns no live match and Google receives a fresh event ID.
        calendar_with(second_api).create_event(
            title="Dinner",
            start="2026-07-19T19:00:00-04:00",
            end="2026-07-19T21:00:00-04:00",
            idempotency_key="telegram:-100:99",
        )

        self.assertNotEqual(
            first_api.insert_calls[0]["body"]["id"],
            second_api.insert_calls[0]["body"]["id"],
        )

    def test_insert_conflict_is_reported_as_an_existing_event(self):
        existing = {
            "id": "calbot-existing",
            "summary": "Dinner",
            "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
            "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            "htmlLink": "https://event/existing",
        }
        api = FakeEventsApi(insert_error=ConflictError(), fetched=existing)

        result = json.loads(
            calendar_with(api).create_event(
                title="Dinner",
                start="2026-07-19T19:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
            )
        )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["id"], "calbot-existing")
        self.assertEqual(len(api.get_calls), 1)

    def test_duplicate_lookup_finds_a_match_on_a_later_page(self):
        api = FakeEventsApi(
            pages={
                None: {"items": [], "nextPageToken": "page-2"},
                "page-2": {
                    "items": [
                        {
                            "id": "existing-event",
                            "summary": "Dinner",
                            "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                            "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
                        }
                    ]
                },
            }
        )

        result = json.loads(
            calendar_with(api).create_event(
                title="Dinner",
                start="2026-07-19T19:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
            )
        )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["id"], "existing-event")
        self.assertEqual(api.insert_calls, [])


class CalendarListAndUpdateTests(unittest.TestCase):
    def test_list_events_fetches_bounded_pages_automatically(self):
        api = FakeEventsApi(
            pages={
                None: {
                    "items": [
                        {
                            "id": "first",
                            "summary": "First",
                            "start": {"date": "2026-07-19"},
                            "end": {"date": "2026-07-20"},
                        }
                    ],
                    "nextPageToken": "page-2",
                },
                "page-2": {
                    "items": [
                        {
                            "id": "second",
                            "summary": "Second",
                            "start": {"date": "2026-07-20"},
                            "end": {"date": "2026-07-21"},
                        }
                    ]
                },
            }
        )

        first_page = json.loads(
            calendar_with(api).list_events(
                "2026-07-19T00:00:00-04:00",
                "2026-07-22T00:00:00-04:00",
            )
        )
        self.assertEqual(first_page["count"], 2)
        self.assertEqual(first_page["events"][0]["id"], "first")
        self.assertEqual(first_page["events"][1]["id"], "second")
        self.assertFalse(first_page["truncated"])
        self.assertEqual(first_page["next_page_token"], "")
        self.assertEqual(api.list_calls[0]["maxResults"], 50)
        self.assertEqual(api.list_calls[1]["pageToken"], "page-2")

    def test_list_events_clips_large_untrusted_text_fields(self):
        api = FakeEventsApi(
            pages={
                None: {
                    "items": [
                        {
                            "id": "large",
                            "summary": "T" * 500,
                            "description": "D" * 5000,
                            "start": {"date": "2026-07-19"},
                            "end": {"date": "2026-07-20"},
                        }
                    ]
                }
            }
        )

        result = json.loads(
            calendar_with(api).list_events(
                "2026-07-19T00:00:00-04:00",
                "2026-07-22T00:00:00-04:00",
            )
        )

        event = result["events"][0]
        self.assertLessEqual(len(event["title"]), 200)
        self.assertLessEqual(len(event["description"]), 500)
        self.assertTrue(event["content_truncated"])

    def test_moving_all_day_start_preserves_all_day_shape_and_duration(self):
        api = FakeEventsApi(
            fetched={
                "id": "trip",
                "summary": "Trip",
                "start": {"date": "2026-07-19"},
                "end": {"date": "2026-07-22"},
            }
        )

        result = json.loads(calendar_with(api).update_event("trip", start="2026-08-01"))

        self.assertEqual(result["status"], "updated")
        body = api.update_calls[0]["body"]
        self.assertEqual(body["start"], {"date": "2026-08-01"})
        self.assertEqual(body["end"], {"date": "2026-08-04"})
        self.assertNotIn("dateTime", body["start"])
        self.assertNotIn("dateTime", body["end"])

    def test_moving_timed_start_preserves_duration(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "summary": "Dinner",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            }
        )

        calendar_with(api).update_event("dinner", start="2026-07-19T20:00:00-04:00")

        body = api.update_calls[0]["body"]
        self.assertEqual(body["start"]["dateTime"], "2026-07-19T20:00:00-04:00")
        self.assertEqual(body["end"]["dateTime"], "2026-07-19T22:00:00-04:00")

    def test_switching_event_kind_requires_both_bounds(self):
        api = FakeEventsApi(
            fetched={
                "id": "trip",
                "summary": "Trip",
                "start": {"date": "2026-07-19"},
                "end": {"date": "2026-07-20"},
            }
        )

        result = json.loads(
            calendar_with(api).update_event("trip", start="2026-07-19T09:00:00-04:00")
        )

        self.assertIn("requires both start and end", result["error"])
        self.assertEqual(api.update_calls, [])

    def test_switching_event_kind_with_both_bounds_never_creates_a_hybrid(self):
        api = FakeEventsApi(
            fetched={
                "id": "trip",
                "summary": "Trip",
                "start": {"date": "2026-07-19"},
                "end": {"date": "2026-07-20"},
            }
        )

        calendar_with(api).update_event(
            "trip",
            start="2026-07-19T09:00:00-04:00",
            end="2026-07-19T17:00:00-04:00",
        )

        body = api.update_calls[0]["body"]
        self.assertIn("dateTime", body["start"])
        self.assertIn("dateTime", body["end"])
        self.assertNotIn("date", body["start"])
        self.assertNotIn("date", body["end"])

    def test_empty_location_and_description_clear_existing_fields(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "summary": "Dinner",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
                "location": "Old place",
                "description": "Old note",
            }
        )

        calendar_with(api).update_event("dinner", location="", description="")

        body = api.update_calls[0]["body"]
        self.assertNotIn("location", body)
        self.assertNotIn("description", body)

    def test_invalid_updated_bounds_are_rejected_without_api_write(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "summary": "Dinner",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            }
        )

        result = json.loads(
            calendar_with(api).update_event(
                "dinner",
                start="2026-07-19T22:00:00-04:00",
                end="2026-07-19T21:00:00-04:00",
            )
        )

        self.assertIn("end must be after start", result["error"])
        self.assertEqual(api.update_calls, [])

    def test_stale_approval_cannot_update_a_changed_event(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "etag": "etag-v2",
                "summary": "Dinner changed by collaborator",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            }
        )

        result = json.loads(
            calendar_with(api).run_tool(
                "update_event",
                {
                    "event_id": "dinner",
                    "title": "Approved title",
                    "_expected_etag": "etag-v1",
                },
            )
        )

        self.assertEqual(result["error_code"], "event_changed_since_approval")
        self.assertEqual(api.update_calls, [])

    def test_stale_approval_cannot_delete_a_changed_event(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "etag": "etag-v2",
                "summary": "Changed dinner",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            }
        )

        result = json.loads(
            calendar_with(api).run_tool(
                "delete_event",
                {"event_id": "dinner", "_expected_etag": "etag-v1"},
            )
        )

        self.assertEqual(result["error_code"], "event_changed_since_approval")
        self.assertEqual(api.delete_calls, [])

    def test_update_dispatch_does_not_mutate_tool_arguments(self):
        api = FakeEventsApi(
            fetched={
                "id": "dinner",
                "summary": "Dinner",
                "start": {"dateTime": "2026-07-19T19:00:00-04:00"},
                "end": {"dateTime": "2026-07-19T21:00:00-04:00"},
            }
        )
        args = {"event_id": "dinner", "title": "Late dinner"}

        calendar_with(api).run_tool("update_event", args)

        self.assertEqual(args, {"event_id": "dinner", "title": "Late dinner"})


if __name__ == "__main__":
    unittest.main()
