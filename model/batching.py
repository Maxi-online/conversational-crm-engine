"""Bounded per-client debounce batches resolve every waiting caller."""

import asyncio


class RequestBatcher:
    def __init__(self, handler, delay=0.1, max_pending=1000):
        if delay < 0:
            raise ValueError("Debounce delay must not be negative")
        self.handler = handler
        self.delay = delay
        self.max_pending = max_pending
        self.pending = {}
        self.tasks = {}
        self.count = 0
        self.closed = False

    async def submit(self, client_id, text, url=None):
        if self.closed:
            raise RuntimeError("The request batcher is closed")
        if self.count >= self.max_pending:
            raise OverflowError("Request queue is full")
        future = asyncio.get_running_loop().create_future()
        self.pending.setdefault(client_id, []).append((text, url, future))
        self.count += 1
        if client_id not in self.tasks:
            self.tasks[client_id] = asyncio.create_task(self._drain(client_id))
        return await future

    async def _drain(self, client_id):
        try:
            while self.pending.get(client_id):
                await asyncio.sleep(self.delay)
                batch = self.pending.pop(client_id, [])
                if not batch:
                    continue
                try:
                    text = " ".join(item[0] for item in batch)
                    url = next((item[1] for item in reversed(batch) if item[1]), None)
                    result = await self.handler(client_id, text, url)
                except Exception as error:
                    for _, _, future in batch:
                        if not future.done():
                            future.set_exception(error)
                else:
                    for _, _, future in batch:
                        if not future.done():
                            future.set_result(result)
                finally:
                    self.count -= len(batch)
        finally:
            self.tasks.pop(client_id, None)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
