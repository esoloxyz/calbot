import unittest
from datetime import datetime, timedelta, timezone

from calbot.authorization import PendingActionStore


class PendingActionStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        self.store = PendingActionStore()
        self.actor = (-100123, 101)

    def propose(self):
        return self.store.propose(
            actor=self.actor,
            tool_name="_calendar_batch",
            tool_args={"actions": [{"name": "delete_event"}]},
            preview="Delete Dinner",
            now=self.now,
        )

    def test_exact_approval_is_one_shot(self):
        pending = self.propose()

        approved = self.store.resolve(self.actor, "APPROVE", now=self.now)
        replay = self.store.resolve(self.actor, "approve", now=self.now)

        self.assertEqual(approved, pending)
        self.assertIsNone(replay)

    def test_approval_rejects_extra_words(self):
        pending = self.propose()

        for text in ("yes", "approve please", "approve Dinner", "do it"):
            with self.subTest(text=text):
                self.assertFalse(pending.matches(text, now=self.now))

    def test_unrelated_message_cancels_pending_action(self):
        self.propose()

        self.assertIsNone(
            self.store.resolve(self.actor, "what is tomorrow?", now=self.now)
        )
        self.assertIsNone(self.store.resolve(self.actor, "approve", now=self.now))

    def test_another_user_cannot_approve_or_cancel(self):
        pending = self.propose()
        other_actor = (self.actor[0], 202)

        self.assertIsNone(self.store.resolve(other_actor, "approve", now=self.now))
        self.assertIs(self.store.get(self.actor, now=self.now), pending)

    def test_users_have_independent_pending_actions(self):
        first = self.propose()
        other_actor = (self.actor[0], 202)
        second = self.store.propose(
            actor=other_actor,
            tool_name="_calendar_batch",
            tool_args={"actions": [{"name": "create_event"}]},
            now=self.now,
        )

        self.assertEqual(self.store.get(self.actor, now=self.now), first)
        self.assertEqual(self.store.get(other_actor, now=self.now), second)

    def test_pending_action_expires(self):
        self.propose()

        self.assertIsNone(
            self.store.resolve(
                self.actor,
                "approve",
                now=self.now + timedelta(minutes=11),
            )
        )

    def test_tool_arguments_are_copied(self):
        arguments = {"actions": [{"name": "create_event"}]}
        pending = self.store.propose(
            actor=self.actor,
            tool_name="_calendar_batch",
            tool_args=arguments,
            now=self.now,
        )

        arguments["actions"][0]["name"] = "delete_event"

        self.assertEqual(
            pending.tool_args["actions"][0]["name"],
            "create_event",
        )


if __name__ == "__main__":
    unittest.main()
