import asyncio
import threading
import time
import unittest

from calbot.concurrency import BlockingBridge


class BlockingBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_serializes_blocking_calls(self):
        bridge = BlockingBridge()
        active = 0
        maximum = 0
        lock = threading.Lock()

        def work():
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1

        await asyncio.gather(bridge.run(work), bridge.run(work))

        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
