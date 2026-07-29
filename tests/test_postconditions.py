import json
import unittest

from calbot.assistant.postconditions import calendar_action_reply


class CalendarActionReplyTests(unittest.TestCase):
    def test_created_event_confirmation_is_lowercase_and_exact(self):
        reply = calendar_action_reply(
            "create_event",
            {
                "title": "Dinner",
                "start": "2026-07-28T19:00:00-04:00",
                "end": "2026-07-28T21:00:00-04:00",
            },
            json.dumps({"status": "created"}),
        )

        self.assertEqual(
            reply,
            "done. dinner is on the calendar for tuesday, july 28 from 7pm to 9pm.",
        )

    def test_all_day_confirmation_uses_inclusive_end_date(self):
        reply = calendar_action_reply(
            "create_event",
            {
                "title": "Ezra and Sarah away",
                "start": "2026-08-08",
                "end": "2026-08-11",
                "all_day": True,
            },
            json.dumps({"status": "created"}),
        )

        self.assertEqual(
            reply,
            (
                "done. ezra and sarah away is on the calendar for "
                "saturday, august 8 through monday, august 10."
            ),
        )

    def test_deleted_event_confirmation_names_previous_slot(self):
        reply = calendar_action_reply(
            "delete_event",
            {
                "title": "Gym",
                "start": "2026-07-31T17:00:00-04:00",
                "end": "2026-07-31T18:00:00-04:00",
            },
            json.dumps({"status": "deleted"}),
        )

        self.assertEqual(
            reply,
            "done. gym was deleted from friday, july 31 from 5pm to 6pm.",
        )

    def test_updated_event_confirmation_uses_verified_final_bounds(self):
        reply = calendar_action_reply(
            "update_event",
            {
                "title": "Dinner",
                "start": "2026-07-28T19:00:00-04:00",
                "end": "2026-07-28T21:00:00-04:00",
            },
            json.dumps(
                {
                    "status": "updated",
                    "title": "Late Dinner",
                    "start": "2026-07-29T20:00:00-04:00",
                    "end": "2026-07-29T22:00:00-04:00",
                    "all_day": False,
                }
            ),
        )

        self.assertEqual(
            reply,
            ("done. late dinner was updated for wednesday, july 29 from 8pm to 10pm."),
        )


if __name__ == "__main__":
    unittest.main()
