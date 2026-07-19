import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_digest import MAX_DIGEST_CHARS, MAX_DIGEST_EVENTS, create_calendar_digest


TZ = ZoneInfo("America/New_York")
START = datetime(2026, 7, 13, tzinfo=TZ)
END = datetime(2026, 7, 20, tzinfo=TZ)


class FakeCalendar:
    def __init__(self, events=None, pages=None):
        self.events = events or []
        self.pages = pages
        self.calls = []

    def list_events(self, time_min, time_max, page_token=""):
        self.calls.append((time_min, time_max, page_token))
        if self.pages is not None:
            return json.dumps(self.pages[page_token])
        return json.dumps({"events": self.events, "count": len(self.events)})


class CalendarDigestTests(unittest.TestCase):
    def test_fetches_and_formats_calendar_deterministically(self):
        calendar = FakeCalendar(
            [{"title": "Dinner", "start": "2026-07-13T19:00:00-04:00"}]
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertIn("Mon, Jul 13 at 7:00 PM — Dinner", reply)
        self.assertEqual(calendar.calls, [(START.isoformat(), END.isoformat(), "")])

    def test_summary_includes_every_event(self):
        calendar = FakeCalendar(
            [
                {"title": "Dentist", "start": "2026-07-14T09:00:00-04:00"},
                {"title": "Flight", "start": "2026-07-16T15:30:00-04:00"},
            ]
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertIn("Dentist", reply)
        self.assertIn("Flight", reply)

    def test_duplicate_titles_keep_distinct_times(self):
        calendar = FakeCalendar(
            [
                {"title": "Lunch", "start": "2026-07-13T12:00:00-04:00"},
                {"title": "Lunch", "start": "2026-07-18T20:00:00-04:00"},
            ]
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertEqual(reply.count("Lunch"), 2)
        self.assertIn("12:00 PM", reply)
        self.assertIn("8:00 PM", reply)

    def test_empty_calendar_still_produces_a_digest(self):
        reply = create_calendar_digest(
            FakeCalendar([]),
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertEqual(
            reply,
            "Your week-ahead summary is clear — nothing is scheduled.",
        )

    def test_fetches_all_pages_before_formatting(self):
        calendar = FakeCalendar(
            pages={
                "": {
                    "events": [
                        {
                            "title": "First page meeting",
                            "start": "2026-07-13T09:00:00-04:00",
                        }
                    ],
                    "truncated": True,
                    "next_page_token": "page-2",
                },
                "page-2": {
                    "events": [
                        {
                            "title": "Second page dinner",
                            "start": "2026-07-14T19:00:00-04:00",
                        }
                    ],
                    "truncated": False,
                    "next_page_token": "",
                },
            }
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertIn("First page meeting", reply)
        self.assertIn("Second page dinner", reply)
        self.assertEqual(
            calendar.calls,
            [
                (START.isoformat(), END.isoformat(), ""),
                (START.isoformat(), END.isoformat(), "page-2"),
            ],
        )

    def test_repeated_page_token_stops_and_discloses_incomplete_digest(self):
        calendar = FakeCalendar(
            pages={
                "": {
                    "events": [{"title": "Only fetched event", "start": "2026-07-13"}],
                    "truncated": True,
                    "next_page_token": "loop",
                },
                "loop": {
                    "events": [],
                    "truncated": True,
                    "next_page_token": "loop",
                },
            }
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertEqual(len(calendar.calls), 2)
        self.assertIn("Only fetched event", reply)
        self.assertIn("More events may exist", reply)

    def test_stops_at_event_cap_and_generated_reply_cannot_hide_truncation(self):
        pages = {}
        for page_number in range(5):
            token = "" if page_number == 0 else f"page-{page_number + 1}"
            next_token = f"page-{page_number + 2}"
            pages[token] = {
                "events": [
                    {
                        "title": f"Event {page_number * 50 + index}",
                        "start": "2026-07-13",
                    }
                    for index in range(50)
                ],
                "truncated": True,
                "next_page_token": next_token,
            }
        calendar = FakeCalendar(pages=pages)
        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertEqual(len(calendar.calls), 4)
        self.assertNotIn("Event 200", reply)
        self.assertIn(f"first {MAX_DIGEST_EVENTS} events", reply)
        self.assertIn("More events may exist", reply)

    def test_rendered_digest_is_bounded_and_discloses_omitted_fetched_events(self):
        calendar = FakeCalendar(
            [
                {
                    "title": f"Event {index} " + "T" * 500,
                    "start": "2026-07-13T09:00:00-04:00",
                    "location": "L" * 500,
                }
                for index in range(MAX_DIGEST_EVENTS)
            ]
        )

        reply = create_calendar_digest(
            calendar,
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertLessEqual(len(reply), MAX_DIGEST_CHARS)
        self.assertIn("additional fetched events omitted", reply)


if __name__ == "__main__":
    unittest.main()
