"""Conversation service and replay-aware Jivo delivery."""

import asyncio
import json
from .batching import RequestBatcher


class CRMService:
    def __init__(self, engine, store, outbound, delay=0.1):
        self.engine = engine
        self.store = store
        self.outbound = outbound
        self.batcher = RequestBatcher(engine.answer, delay)
        self.event_locks = {}

    async def chat(self, client_id, text, url=None):
        return await self.batcher.submit(client_id, text, url)

    async def process_event(self, event):
        lock = self.event_locks.setdefault(event.id, asyncio.Lock())
        async with lock:
            record = self.store.event(event.id)
            if record and record["client_id"] != event.client_id:
                raise ValueError("An event identifier was reused for another client")
            if record and record["state"] == "delivered":
                return {"replayed": True}
            if record and record["state"] == "processing":
                return {"pending_recovery": True}
            if record is None and not self.store.reserve_event(event.id, event.client_id):
                return {"in_progress": True}
            try:
                if record and record["response_json"]:
                    answer = json.loads(record["response_json"])
                else:
                    answer = await self.chat(event.client_id, event.message.text, event.sender.url)
                    self.store.save_event_response(event.id, answer)
                await self.outbound.send_jivo(
                    event.id, event.client_id, event.chat_id, answer["message"]
                )
                self.store.complete_event(event.id)
                return {"delivered": True}
            except Exception:
                self.store.fail_event(event.id)
                raise

    async def close(self):
        await self.batcher.close()
        close = getattr(self.engine.agents, "close", None)
        if close:
            await close()
