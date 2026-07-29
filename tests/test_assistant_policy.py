import unittest

from calbot.assistant.policy import CALENDAR_ASSISTANT_POLICY


class AssistantCalendarPolicyTests(unittest.TestCase):
    def test_reads_current_calendar_before_schedule_claims(self):
        self.assertIn("Use list_events", CALENDAR_ASSISTANT_POLICY)

    def test_application_owns_write_approval(self):
        self.assertIn("Do not ask for confirmation yourself", CALENDAR_ASSISTANT_POLICY)
        self.assertIn("reply `approve`", CALENDAR_ASSISTANT_POLICY)

    def test_ambiguous_requests_are_not_guessed(self):
        self.assertIn("ask one concise", CALENDAR_ASSISTANT_POLICY)


if __name__ == "__main__":
    unittest.main()
