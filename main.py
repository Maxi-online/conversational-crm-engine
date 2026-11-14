"""Authenticated chat, consented leads and neutral Jivo webhooks."""

from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
from typing import Optional
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from model.config import Settings
from model.database import ConversationStore
from model.agents import ModelAgents
from model.models import ConversationEngine
from model.retrieval import CatalogIndex
from model.integrations import OutboundAdapters
from model.service import CRMService

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    client_id: str = Field(min_length=1, max_length=128)
    url: Optional[str] = Field(default=None, max_length=2048)


class Sender(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    type: str = "TEXT"


class JivoEvent(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)
    event: str = "CLIENT_MESSAGE"
    sender: Sender = Field(default_factory=Sender)
    message: Message


class LeadRequest(BaseModel):
    lead_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=3, max_length=40)
    consent: bool = Field(strict=True)


def create_service(settings=None):
    settings = settings or Settings.from_environment()
    store = ConversationStore(settings.database_path)
    index = CatalogIndex(settings.catalog_path, settings.retrieval_backend)
    agents = ModelAgents(settings.model, settings.specialist_model)
    engine = ConversationEngine(store, index, agents)
    return CRMService(engine, store, OutboundAdapters(), settings.debounce_seconds)


def create_app(service_factory=create_service):
    @asynccontextmanager
    async def lifespan(application):
        service = service_factory()
        application.state.service = service
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="Conversational CRM Engine", version="1.0.0", lifespan=lifespan)

    async def require_access(request: Request):
        expected = os.getenv("CRM_API_TOKEN", "")
        if not expected:
            raise HTTPException(503, "CRM_API_TOKEN must be configured")
        if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + expected):
            raise HTTPException(401, "Invalid API access token")

    async def require_webhook(request: Request):
        expected = os.getenv("JIVO_WEBHOOK_SECRET", "")
        if not expected:
            raise HTTPException(503, "JIVO_WEBHOOK_SECRET must be configured")
        if not hmac.compare_digest(request.headers.get("X-Webhook-Secret", ""), expected):
            raise HTTPException(401, "Invalid webhook secret")

    async def require_channel(channel_token: str):
        expected = os.getenv("JIVO_CHANNEL_TOKEN", "")
        if not expected:
            raise HTTPException(503, "JIVO_CHANNEL_TOKEN must be configured")
        if not hmac.compare_digest(channel_token, expected):
            raise HTTPException(401, "Invalid channel token")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "conversational-crm-engine"}

    @app.post("/chat", dependencies=[Depends(require_access)])
    async def chat(body: ChatRequest, request: Request):
        try:
            return await request.app.state.service.chat(body.client_id, body.text, body.url)
        except OverflowError as error:
            raise HTTPException(429, "Request queue is full") from error
        except ValueError as error:
            raise HTTPException(
                503, "Conversation provider configuration or output is invalid"
            ) from error
        except Exception as error:
            raise HTTPException(502, "Conversation generation failed") from error

    @app.delete("/conversations/{client_id}", dependencies=[Depends(require_access)])
    async def forget(client_id: str, request: Request):
        await request.app.state.service.engine.forget(client_id)
        return {"message": "Conversation removed"}

    @app.post("/leads", dependencies=[Depends(require_access)])
    async def lead(body: LeadRequest, request: Request):
        if not body.consent:
            raise HTTPException(422, "Explicit contact-processing consent is required")
        service = request.app.state.service
        payload = body.model_dump()
        existing = service.store.get_lead(body.lead_id)
        if existing and (
            existing["client_id"] != body.client_id
            or existing["payload_json"] != json.dumps(payload, ensure_ascii=False)
        ):
            raise HTTPException(409, "Lead identifier was reused with different details")
        inserted = service.store.save_lead(body.lead_id, body.client_id, payload)
        forwarded = bool(existing and existing["forwarded"])
        if not forwarded:
            try:
                forwarded = await service.outbound.send_lead(body.lead_id, payload)
                if forwarded:
                    service.store.mark_lead_forwarded(body.lead_id)
            except Exception as error:
                raise HTTPException(
                    502, "Lead saved; outbound delivery can be retried with the same lead_id"
                ) from error
        if inserted:
            try:
                await service.outbound.notify_lead(body.lead_id)
            except Exception:
                logger.warning("Optional lead notification failed")
        return {"saved": True, "forwarded": forwarded, "lead_id": body.lead_id}

    @app.post("/webhooks/jivo/{channel_token}", dependencies=[Depends(require_channel)])
    @app.post("/webhooks/jivo", dependencies=[Depends(require_webhook)])
    async def webhook(body: JivoEvent, request: Request, tasks: BackgroundTasks):
        if body.event != "CLIENT_MESSAGE" or body.message.type != "TEXT":
            return {"status": "ignored"}
        service = request.app.state.service
        record = service.store.event(body.id)
        if record and record["client_id"] != body.client_id:
            raise HTTPException(409, "Event identifier belongs to another client")
        if record and record["state"] == "delivered":
            return {"status": "already_delivered"}

        async def process():
            try:
                await service.process_event(body)
            except Exception:
                logger.warning("Webhook processing failed; inspect the event ledger")

        tasks.add_task(process)
        return {"status": "accepted", "event_id": body.id}

    return app


app = create_app()
