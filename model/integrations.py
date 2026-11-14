"""Optional outbound adapters; destinations and credentials are operator-supplied."""

import os
import time
from urllib.parse import urlparse
import httpx


def configured_url(variable):
    value = os.getenv(variable, "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError(variable + " must be an HTTP(S) URL without embedded credentials")
    return value


class OutboundAdapters:
    async def send_jivo(self, event_id, client_id, chat_id, message):
        url = configured_url("JIVO_SEND_URL")
        if not url:
            raise ValueError("JIVO_SEND_URL must be explicitly configured")
        access = os.getenv("JIVO_API_TOKEN", "")
        headers = {"Authorization": "Bearer " + access} if access else {}
        payload = {
            "id": event_id + ":reply",
            "client_id": client_id,
            "chat_id": chat_id,
            "event": "BOT_MESSAGE",
            "message": {
                "type": "MARKDOWN",
                "content": message,
                "text": message,
                "timestamp": int(time.time()),
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

    async def send_lead(self, lead_id, payload):
        url = configured_url("LEAD_API_URL")
        if not url:
            return False
        access = os.getenv("LEAD_API_TOKEN", "")
        headers = {"Idempotency-Key": lead_id}
        if access:
            headers["Authorization"] = "Bearer " + access
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        return True

    async def notify_lead(self, lead_id):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return False
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.telegram.org/bot" + token + "/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "A consented follow-up request was saved. Reference: " + lead_id,
                },
            )
            response.raise_for_status()
        return True
