import unittest

from calbot.assistant.access import CalendarToolAccess, calendar_tool_access


class CalendarToolAccessTests(unittest.TestCase):
    def test_acknowledgments_and_small_talk_authorize_no_tools(self):
        for text in (
            "good stuff calbot. youre fixed",
            "thanks!",
            "lol",
            "good morning",
            "see you tomorrow",
            "thanks, friday at 8 works",
            "don't add dinner tomorrow",
        ):
            with self.subTest(text=text):
                self.assertIs(
                    calendar_tool_access(text),
                    CalendarToolAccess.NONE,
                )

    def test_explicit_calendar_changes_authorize_writes(self):
        for text in (
            "add dinner on saturday at 8pm",
            "move dinner to 8",
            "cancel the dentist appointment",
            "put a reminder on the cal for later today",
            "i need you to add the dentist on tuesday",
        ):
            with self.subTest(text=text):
                self.assertIs(
                    calendar_tool_access(text),
                    CalendarToolAccess.WRITE,
                )

    def test_terse_event_requests_still_authorize_writes(self):
        for text in (
            "dinner at lilia saturday at 8",
            "dentist tomorrow at 3pm",
            "alyssa's birthday august 29th",
        ):
            with self.subTest(text=text):
                self.assertIs(
                    calendar_tool_access(text),
                    CalendarToolAccess.WRITE,
                )

    def test_calendar_questions_authorize_reads_only(self):
        for text in (
            "what do we have this weekend?",
            "are we free friday night?",
            "when is the kaufman bbq?",
            "show me the calendar",
            "did you add them?",
            "what's my schedule tomorrow?",
            "can you see if we have anything friday?",
        ):
            with self.subTest(text=text):
                self.assertIs(
                    calendar_tool_access(text),
                    CalendarToolAccess.READ,
                )

    def test_answer_to_a_calendar_followup_inherits_original_access(self):
        self.assertIs(
            calendar_tool_access(
                "the one at 7",
                previous_user_text="move dinner",
                previous_assistant_text="which dinner do you mean?",
            ),
            CalendarToolAccess.WRITE,
        )

    def test_acknowledgment_after_confirmation_does_not_inherit_access(self):
        self.assertIs(
            calendar_tool_access(
                "good stuff calbot. youre fixed",
                previous_user_text="add the kaufman bbq",
                previous_assistant_text=(
                    "done. kaufman bbq is on the calendar for august 22."
                ),
            ),
            CalendarToolAccess.NONE,
        )


if __name__ == "__main__":
    unittest.main()
