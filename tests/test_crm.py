import asyncio
import json
import secrets
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from demo import DemoAgents
from main import create_app, JivoEvent
from model.batching import RequestBatcher
from model.config import ROOT
from model.database import ConversationStore
from model.integrations import OutboundAdapters, configured_url
from model.models import ConversationEngine
from model.retrieval import CatalogIndex
from model.service import CRMService


@pytest.mark.asyncio
async def test_batched_requests_resolve_every_waiting_caller():
    calls = []

    async def handler(client, text, url):
        calls.append((client, text))
        return {"message": text}

    batcher = RequestBatcher(handler, delay=0.01)
    responses = await asyncio.wait_for(
        asyncio.gather(*(batcher.submit("reader", word) for word in ["first", "second", "third"])),
        2,
    )
    await batcher.close()
    assert len(calls) == 1 and all(r["message"] == "first second third" for r in responses)
    assert batcher.count == 0


@pytest.mark.asyncio
async def test_failed_batch_fails_all_waiters_and_releases_capacity():
    async def handler(*args):
        raise ValueError("Synthetic provider failure")

    batcher = RequestBatcher(handler, delay=0.01)
    results = await asyncio.wait_for(
        asyncio.gather(
            batcher.submit("reader", "one"), batcher.submit("reader", "two"), return_exceptions=True
        ),
        2,
    )
    await batcher.close()
    assert all(isinstance(r, ValueError) for r in results) and batcher.count == 0


@pytest.mark.asyncio
async def test_queue_capacity_is_bounded():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(*args):
        started.set()
        await release.wait()
        return {"message": "done"}

    batcher = RequestBatcher(handler, delay=0, max_pending=1)
    first = asyncio.create_task(batcher.submit("one", "one"))
    await started.wait()
    with pytest.raises(OverflowError):
        await batcher.submit("two", "two")
    release.set()
    await first
    await batcher.close()


@pytest.mark.asyncio
async def test_entity_memory_and_history_are_separate_per_customer(tmp_path):
    store = ConversationStore(tmp_path / "state.sqlite3")
    engine = ConversationEngine(store, CatalogIndex(ROOT / "bases/database.md"), DemoAgents())
    await asyncio.gather(
        engine.answer("alpha", "Machine learning for alpha"),
        engine.answer("beta", "Data engineering for beta"),
    )
    a, b = store.load("alpha"), store.load("beta")
    assert a["entities"]["needs"] == ["Machine learning for alpha"]
    assert b["entities"]["needs"] == ["Data engineering for beta"]
    assert "beta" not in json.dumps(a) and "alpha" not in json.dumps(b)


@pytest.mark.asyncio
async def test_unknown_router_target_does_not_commit_a_partial_conversation(tmp_path):
    class InvalidRouter(DemoAgents):
        async def complete(self, role, payload):
            return (
                {"specialists": ["Unsupported"]}
                if role == "router"
                else await super().complete(role, payload)
            )

    store = ConversationStore(tmp_path / "state.sqlite3")
    engine = ConversationEngine(store, CatalogIndex(ROOT / "bases/database.md"), InvalidRouter())
    with pytest.raises(ValueError):
        await engine.answer("reader", "Data engineering")
    assert store.load("reader") is None


class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.agents = SimpleNamespace()

    async def answer(self, client_id, text, url=None):
        self.calls += 1
        return {
            "message": "A synthetic grounded reply",
            "summarized_history": "Demo",
            "specialists": [],
            "evidence": [],
        }

    async def forget(self, client_id):
        pass


class FakeOutbound:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first
        self.leads = []

    async def send_jivo(self, *args):
        self.calls.append(args)
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("Synthetic delivery failure")

    async def send_lead(self, lead_id, payload):
        self.leads.append((lead_id, payload))
        return True

    async def notify_lead(self, *args):
        return False


def service_for(tmp_path, fail_first=False):
    engine = FakeEngine()
    store = ConversationStore(tmp_path / "state.sqlite3")
    outbound = FakeOutbound(fail_first)
    return CRMService(engine, store, outbound, delay=0)


def event():
    return JivoEvent(
        id="demo-event",
        client_id="demo-reader",
        chat_id="demo-chat",
        message={"text": "Tell me about the catalog"},
    )


@pytest.mark.asyncio
async def test_failed_delivery_reuses_persisted_answer_on_retry(tmp_path):
    service = service_for(tmp_path, True)
    with pytest.raises(RuntimeError):
        await service.process_event(event())
    assert service.store.event("demo-event")["state"] == "ready"
    assert service.engine.calls == 1
    await service.process_event(event())
    await service.process_event(event())
    assert service.engine.calls == 1 and len(service.outbound.calls) == 2
    assert service.store.event("demo-event")["state"] == "delivered"
    await service.close()


def test_leads_require_explicit_consent_and_are_idempotent(tmp_path, monkeypatch):
    access = secrets.token_urlsafe(24)
    monkeypatch.setenv("CRM_API_TOKEN", access)
    service = service_for(tmp_path)
    with TestClient(create_app(lambda: service)) as client:
        headers = {"Authorization": "Bearer " + access}
        payload = {
            "lead_id": "demo-lead",
            "client_id": "demo-reader",
            "name": "Sample Reader",
            "phone": "fictional-contact",
            "consent": False,
        }
        assert client.post("/leads", headers=headers, json=payload).status_code == 422
        assert service.store.get_lead("demo-lead") is None
        payload["consent"] = True
        assert client.post("/leads", headers=headers, json=payload).json()["forwarded"]
        assert client.post("/leads", headers=headers, json=payload).status_code == 200
        assert len(service.outbound.leads) == 1
        payload["name"] = "Another Sample"
        assert client.post("/leads", headers=headers, json=payload).status_code == 409


def test_both_webhook_authentication_routes_and_replay(tmp_path, monkeypatch):
    channel = secrets.token_urlsafe(24)
    proxy = secrets.token_urlsafe(24)
    monkeypatch.setenv("JIVO_CHANNEL_TOKEN", channel)
    monkeypatch.setenv("JIVO_WEBHOOK_SECRET", proxy)
    service = service_for(tmp_path)
    with TestClient(create_app(lambda: service)) as client:
        payload = event().model_dump()
        assert client.post("/webhooks/jivo", json=payload).status_code == 401
        assert (
            client.post(
                "/webhooks/jivo", headers={"X-Webhook-Secret": proxy}, json=payload
            ).status_code
            == 200
        )
        assert (
            client.post("/webhooks/jivo/" + channel, json=payload).json()["status"]
            == "already_delivered"
        )
        assert len(service.outbound.calls) == 1


@pytest.mark.asyncio
async def test_jivo_adapter_includes_fallback_text_and_stable_event_id(monkeypatch):
    monkeypatch.setenv("JIVO_SEND_URL", "https://example.org/demo-webhook")
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs),
    )
    adapter = OutboundAdapters()
    await adapter.send_jivo("event-one", "reader", "chat", "**Grounded** answer")
    await adapter.send_jivo("event-two", "reader", "chat", "Next answer")
    assert requests[0]["id"] != requests[1]["id"]
    assert requests[0]["message"]["text"] == "**Grounded** answer"
    assert isinstance(requests[0]["message"]["timestamp"], int)


def test_outbound_configuration_has_no_client_fallback(monkeypatch):
    monkeypatch.delenv("LEAD_API_URL", raising=False)
    assert configured_url("LEAD_API_URL") is None
    monkeypatch.setenv(
        "LEAD_API_URL",
        "https://" + secrets.token_hex(3) + ":" + secrets.token_hex(3) + "@example.org/",
    )
    with pytest.raises(ValueError):
        configured_url("LEAD_API_URL")
