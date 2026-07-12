import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from calendar_digest import create_calendar_digest


TZ = ZoneInfo("America/New_York")
START = datetime(2026, 7, 13, tzinfo=TZ)
END = datetime(2026, 7, 20, tzinfo=TZ)


class FakeCalendar:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def list_events(self, time_min, time_max):
        self.calls.append((time_min, time_max))
        return json.dumps({"events": self.events, "count": len(self.events)})


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)]
        )


def fake_claude(reply):
    return SimpleNamespace(messages=FakeMessages(reply))


class CalendarDigestTests(unittest.TestCase):
    def test_fetches_calendar_directly_without_tools_or_history(self):
        calendar = FakeCalendar(
            [{"title": "Dinner", "start": "2026-07-13T19:00:00-04:00"}]
        )
        claude = fake_claude("Sunday: Dinner at 7 PM.")

        reply = create_calendar_digest(
            calendar, claude, "model", "week-ahead summary", START, END, "America/New_York"
        )

        self.assertEqual(reply, "Sunday: Dinner at 7 PM.")
        self.assertEqual(calendar.calls, [(START.isoformat(), END.isoformat())])
        call = claude.messages.calls[0]
        self.assertNotIn("tools", call)
        self.assertEqual(len(call["messages"]), 1)
        self.assertIn("Dinner", call["messages"][0]["content"])

    def test_pass_uses_deterministic_summary_with_every_event(self):
        calendar = FakeCalendar(
            [
                {"title": "Dentist", "start": "2026-07-14T09:00:00-04:00"},
                {"title": "Flight", "start": "2026-07-16T15:30:00-04:00"},
            ]
        )

        reply = create_calendar_digest(
            calendar,
            fake_claude("PASS"),
            "model",
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertIn("Dentist", reply)
        self.assertIn("Flight", reply)
        self.assertNotEqual(reply, "PASS")

    def test_omitted_event_uses_complete_fallback(self):
        calendar = FakeCalendar(
            [
                {"title": "Lunch", "start": "2026-07-13T12:00:00-04:00"},
                {"title": "Concert", "start": "2026-07-18T20:00:00-04:00"},
            ]
        )

        reply = create_calendar_digest(
            calendar,
            fake_claude("You have Lunch on Monday."),
            "model",
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertIn("Lunch", reply)
        self.assertIn("Concert", reply)

    def test_empty_calendar_still_produces_a_digest(self):
        reply = create_calendar_digest(
            FakeCalendar([]),
            fake_claude("Your week is wide open!"),
            "model",
            "week-ahead summary",
            START,
            END,
            "America/New_York",
        )

        self.assertEqual(reply, "Your week is wide open!")


if __name__ == "__main__":
    unittest.main()
