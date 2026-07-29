"""Async coordination for the synchronous API clients."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable


log = logging.getLogger("assistant-bot")


class BlockingBridge:
    """Run one blocking API operation at a time without blocking Telegram."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def run(self, function: Callable, *args, **kwargs):
        async with self._lock:
            worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError as cancellation:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                try:
                    worker.result()
                except Exception:
                    log.exception(
                        "Blocking operation failed after its caller was cancelled"
                    )
                raise cancellation
