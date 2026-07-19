import unittest
from datetime import datetime, timedelta, timezone

from action_authorization import PendingActionStore


class PendingActionStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        self.store = PendingActionStore(token_factory=lambda: "P7K4M2")
        self.actor = (-100123, 101)

    def test_exact_calendar_approval_is_one_shot(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="delete_event",
            tool_args={"event_id": "victim-event"},
            now=self.now,
        )

        approved = self.store.resolve(
            self.actor, pending.confirmation_prompt, now=self.now
        )
        replay = self.store.resolve(
            self.actor, pending.confirmation_prompt, now=self.now
        )

        self.assertEqual(approved.tool_name, "delete_event")
        self.assertEqual(approved.tool_args, {"event_id": "victim-event"})
        self.assertIsNone(replay)

    def test_payment_approval_requires_token_and_exact_sub_cent_amount(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="tempo_call_service",
            tool_args={"url": "https://service.example/paid"},
            amount="0.003",
            now=self.now,
        )

        self.assertEqual(pending.confirmation_prompt, "approve P7K4M2 $0.003")
        for reply in (
            "yes",
            "approve",
            "approve $0.003",
            "approve P7K4M2 $0.01",
            "approve WRONG1 $0.003",
        ):
            with self.subTest(reply=reply):
                self.assertFalse(pending.matches(reply, now=self.now))
        self.assertTrue(pending.matches("APPROVE P7K4M2 $0.003", now=self.now))

    def test_zero_spend_ceiling_is_preserved_without_changing_reply_phrase(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="tempo_call_service",
            tool_args={"url": "https://service.example/free"},
            spend_limit="0",
            now=self.now,
        )

        self.assertEqual(pending.spend_limit, "0.00")
        self.assertEqual(pending.confirmation_prompt, "approve P7K4M2")

    def test_approval_spend_precision_matches_the_pinned_cli(self):
        with self.assertRaisesRegex(ValueError, "precision"):
            self.store.propose(
                actor=self.actor,
                tool_name="tempo_call_service",
                tool_args={},
                spend_limit="0.0000009",
                now=self.now,
            )

    def test_unrelated_message_from_owner_cancels_pending_action(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="update_event",
            tool_args={"event_id": "dinner", "start": "2026-07-19T20:00:00-04:00"},
            now=self.now,
        )

        self.assertIsNone(
            self.store.resolve(self.actor, "what is the weather?", now=self.now)
        )
        self.assertIsNone(
            self.store.resolve(self.actor, pending.confirmation_prompt, now=self.now)
        )

    def test_group_member_cannot_approve_or_cancel_another_users_action(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="delete_event",
            tool_args={"event_id": "dinner"},
            now=self.now,
        )
        other_actor = (self.actor[0], 202)

        self.assertIsNone(
            self.store.resolve(other_actor, pending.confirmation_prompt, now=self.now)
        )
        self.assertIs(self.store.get(self.actor, now=self.now), pending)

    def test_two_users_in_one_chat_have_independent_pending_actions(self):
        first = self.store.propose(
            actor=self.actor,
            tool_name="delete_event",
            tool_args={"event_id": "first"},
            now=self.now,
        )
        second_actor = (self.actor[0], 202)
        second = self.store.propose(
            actor=second_actor,
            tool_name="delete_event",
            tool_args={"event_id": "second"},
            now=self.now,
        )

        self.assertEqual(self.store.get(self.actor, now=self.now), first)
        self.assertEqual(self.store.get(second_actor, now=self.now), second)

    def test_pending_action_expires(self):
        pending = self.store.propose(
            actor=self.actor,
            tool_name="delete_event",
            tool_args={"event_id": "dinner"},
            now=self.now,
        )

        self.assertIsNone(
            self.store.resolve(
                self.actor,
                pending.confirmation_prompt,
                now=self.now + timedelta(minutes=11),
            )
        )


if __name__ == "__main__":
    unittest.main()
