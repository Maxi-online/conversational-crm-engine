"""Offline specialist workflow with a deterministic model adapter."""

import asyncio
import json
from pathlib import Path
import tempfile
from model.agents import EXTRACTORS, SPECIALISTS
from model.config import ROOT
from model.database import ConversationStore
from model.models import ConversationEngine
from model.retrieval import CatalogIndex


class DemoAgents:
    async def complete(self, role, payload):
        if role in EXTRACTORS:
            return {"items": [payload["question"][:100]] if role == "needs" else []}
        if role == "topic":
            return {"query": payload["question"]}
        if role == "summary":
            return {"summary": "Synthetic conversation: " + payload["question"]}
        if role == "router":
            return {"specialists": ["Presentation_Specialist", "Needs_Identification_Specialist"]}
        if role in SPECIALISTS:
            return {
                "answer": "The example catalog includes applied machine learning and data engineering. What is your learning goal?"
            }
        if role == "final":
            evidence = payload["evidence"]
            return {
                "answer": evidence[0]["text"][:220]
                if evidence
                else "The example catalog does not contain enough information. What would you like to learn?"
            }
        raise ValueError("Unexpected demo role")


async def run_demo():
    with tempfile.TemporaryDirectory(prefix="crm-offline-demo-") as folder:
        store = ConversationStore(Path(folder) / "conversations.sqlite3")
        engine = ConversationEngine(store, CatalogIndex(ROOT / "bases/database.md"), DemoAgents())
        first = await engine.answer(
            "demo-reader-a", "What does the applied machine learning course cover?"
        )
        second = await engine.answer(
            "demo-reader-b", "What does the data engineering course cover?"
        )
        return {
            "mode": "offline deterministic adapter",
            "live_model_requests": 0,
            "first_response": first,
            "second_response": second,
            "separate_conversations": store.load("demo-reader-a")["history"]
            != store.load("demo-reader-b")["history"],
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_demo()), indent=2))
