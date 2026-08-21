import unittest

from calbot.state import InMemoryStateStore


class InMemoryStateStoreTests(unittest.TestCase):
    def test_records_context_receipts_and_idempotent_reply(self):
        store = InMemoryStateStore()
        store.record_turn(
            chat_id=-100,
            user_id=1,
            actor_name="Ezra",
            user_text="add dinner",
            assistant_text="done.",
        )
        store.record_receipts(
            chat_id=-100,
            user_id=1,
            request_id="telegram:-100:9",
            receipts=(
                {
                    "action": "create_event",
                    "status": "created",
                    "event_id": "dinner-1",
                },
            ),
        )
        store.cache_reply("telegram:-100:9", "done.")
        store.cache_reply("telegram:-100:9", "different")

        self.assertEqual(store.recent_messages(-100)[0]["actor_name"], "Ezra")
        self.assertEqual(store.recent_receipts(-100)[0]["event_id"], "dinner-1")
        self.assertEqual(store.cached_reply("telegram:-100:9"), "done.")


if __name__ == "__main__":
    unittest.main()
