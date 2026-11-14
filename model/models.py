"""Per-customer conversation workflow derived from the specialist ensemble."""

import asyncio
from .config import EXTRACTORS, SPECIALISTS
from .catalog_parser import program_name_and_description_parser


def string_field(value, key):
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError("Missing nonempty role field: " + key)
    return result.strip()


def string_list(value, key):
    values = value.get(key)
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError("Expected a list of strings: " + key)
    return [v.strip() for v in values if v.strip()]


class ConversationEngine:
    def __init__(self, store, index, agents):
        self.store = store
        self.index = index
        self.agents = agents
        self.locks = {}

    async def answer(self, client_id, text, url=None):
        lock = self.locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            state = self.store.load(client_id) or {
                "history": [],
                "summary": "",
                "entities": {k: [] for k in EXTRACTORS},
            }
            prior = list(state["history"])
            context = {
                "question": text,
                "history": prior[-12:],
                "summary": state["summary"],
                "page": program_name_and_description_parser(url),
            }
            extracted = await asyncio.gather(
                *(self.agents.complete(role, context) for role in EXTRACTORS)
            )
            for role, result in zip(EXTRACTORS, extracted):
                items = string_list(result, "items")
                state["entities"][role] = list(dict.fromkeys(state["entities"][role] + items))[-30:]
            topic, summary = await asyncio.gather(
                self.agents.complete("topic", context), self.agents.complete("summary", context)
            )
            query = string_field(topic, "query")
            state["summary"] = string_field(summary, "summary")
            documents = await asyncio.to_thread(self.index.search, query, 3)
            evidence = [
                {"text": doc.page_content, "metadata": dict(doc.metadata)} for doc in documents
            ]
            payload = {
                **context,
                "summary": state["summary"],
                "entities": state["entities"],
                "evidence": evidence,
            }
            router = await self.agents.complete("router", payload)
            selected = list(dict.fromkeys(string_list(router, "specialists")))
            if not selected or any(role not in SPECIALISTS for role in selected):
                raise ValueError("The router selected an unsupported specialist")
            replies = await asyncio.gather(
                *(self.agents.complete(role, payload) for role in selected)
            )
            specialists = {
                role: string_field(reply, "answer") for role, reply in zip(selected, replies)
            }
            final = await self.agents.complete(
                "final", {**payload, "specialist_answers": specialists}
            )
            answer = string_field(final, "answer")
            state["history"] = (
                prior
                + [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
            )[-24:]
            self.store.save(client_id, state)
            return {
                "message": answer,
                "summarized_history": state["summary"],
                "specialists": selected,
                "evidence": [dict(doc.metadata) for doc in documents],
            }

    async def forget(self, client_id):
        lock = self.locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            self.store.forget(client_id)
