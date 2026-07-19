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

from calendar_client import CalendarClient


class FakeRequest:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeEventsApi:
    def __init__(self, listed=None, created=None, insert_error=None, fetched=None):
        self.listed = listed or []
        self.created = created or {"id": "new-event", "htmlLink": "https://event/new"}
        self.insert_error = insert_error
        self.fetched = fetched
        self.list_calls = []
        self.insert_calls = []
        self.get_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return FakeRequest({"items": self.listed})

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return FakeRequest(self.created, self.insert_error)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.fetched)


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


if __name__ == "__main__":
    unittest.main()
