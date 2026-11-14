# Conversational CRM Engine

A sanitized conversation-to-CRM system that combines **parallel specialist agents, catalog-grounded retrieval, customer-scoped state, debounced request processing and replay-aware Jivo delivery**.

This repository preserves the central workflow of an earlier domain-specific consultant while replacing the original catalog, integrations and deployment settings with neutral configuration and fictional English examples. It is an AI integration engineering case, not a claim of a newly trained model or independently measured sales uplift.

## Implemented capabilities

- Five parallel extraction roles maintain requirements, explained benefits, objections, addressed objections and stated commercial constraints.
- Topic extraction and dialogue summarization run concurrently.
- Catalog retrieval supplies evidence to a specialist router and selected specialist ensemble.
- A final advisor combines the specialist replies into one grounded answer.
- SQLite stores a separate history, summary and entity snapshot for each client.
- A bounded per-client debounce queue combines short bursts and resolves every waiting request.
- A durable event ledger stores generated replies before outbound Jivo delivery. Delivery retries reuse that reply.
- A separate lead endpoint requires explicit consent and supports stable lead identifiers.
- Outbound Jivo, lead and optional Telegram-notification destinations are configured by the operator.
- HTTP authentication, offline examples, meaningful regression tests and publication checks are included.

## Conversation workflow

~~~mermaid
flowchart TD
    Input[Authenticated client message] --> Queue[Bounded per-client batch]
    Queue --> State[Load client snapshot]
    State --> Extract[Five extraction roles in parallel]
    Extract --> Context[Topic and summary in parallel]
    Context --> Retrieve[Catalog evidence]
    Retrieve --> Router[Select specialist roles]
    Router --> Specialists[Selected specialists in parallel]
    Specialists --> Final[Final grounded advisor]
    Final --> Persist[Save client snapshot]
    Persist --> API[Return HTTP reply]
    Persist --> Ledger[Persist webhook response]
    Ledger --> Jivo[Optional outbound Jivo delivery]
~~~

The five specialist roles are Needs Identification, Objection Handler, Presentation, Call Schedule and Closure. The model may select only registered names. Invalid role output fails before a new conversation snapshot is saved.

A turn uses five extraction calls, two context calls, one router call, one final call and the selected specialist calls: normally 10–14 chat requests. Parallelism reduces the number of sequential phases, not the number of billed model requests. Dense indexing adds embedding requests when that backend is selected. No cost or latency benchmark is claimed.

## Run the complete offline example

~~~bash
git clone https://github.com/Maxi-online/conversational-crm-engine.git
cd conversational-crm-engine
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python demo.py
python -m pytest -q
~~~

Windows activation:

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

The offline example runs the real orchestration, retrieval and persistence code with deterministic role adapters. It processes two distinct customers and demonstrates separate state. It does not call OpenAI, Jivo, Telegram or a lead endpoint. Its generated text is not a model-quality result.

## Configuration

| Variable | Purpose |
| --- | --- |
| OPENAI_API_KEY | Credential for live chat and optional dense embeddings |
| CRM_API_TOKEN | Operator/gateway credential for chat and lead endpoints |
| OPENAI_MODEL | Final-advisor model; default gpt-4.1-mini |
| OPENAI_SPECIALIST_MODEL | Optional separate specialist model |
| CRM_DATABASE_PATH | SQLite state path; default var/conversations.sqlite3 |
| CRM_CATALOG_PATH | Operator-owned Markdown catalog |
| CRM_RETRIEVAL_BACKEND | tfidf by default; faiss enables hosted dense embeddings |
| CRM_DEBOUNCE_SECONDS | Delay used to combine a customer's short burst |
| JIVO_SEND_URL | Explicit Bot API destination, including operator-managed channel information |
| JIVO_API_TOKEN | Optional bearer header when that destination is an authenticated gateway |
| JIVO_CHANNEL_TOKEN | Token for the direct Jivo callback route |
| JIVO_WEBHOOK_SECRET | Header credential for the gateway callback route |
| LEAD_API_URL | Optional lead delivery destination |
| LEAD_API_TOKEN | Optional bearer credential for that destination |
| TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID | Optional consented-lead notification destination |

The default catalog is fictional and contains no real prices, contacts, contractual terms or customer records. There are no built-in customer endpoint fallbacks. Do not place credentials in tracked Python, Markdown, configuration or container files.

## HTTP service

After configuring the live process environment:

~~~bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
~~~

GET /health is independent of model access. POST /chat uses the CRM_API_TOKEN bearer credential.

~~~python
import os
import httpx

response = httpx.post(
    "http://127.0.0.1:8000/chat",
    headers={"Authorization": "Bearer " + os.environ["CRM_API_TOKEN"]},
    json={
        "client_id": "demo-reader",
        "text": "What does the applied machine learning course cover?",
        "url": "https://example.org/courses/applied-machine-learning",
    },
    timeout=180,
)
response.raise_for_status()
print(response.json())
~~~

The response contains message, summarized_history, selected specialists and evidence identifiers. DELETE /conversations/demo-reader removes that client's conversation snapshot.

The shared API credential authenticates an operator/gateway. It does not establish independent end-user identity or tenant authorization; the host binds client_id to its own authenticated user.

## Jivo and delivery semantics

Two inbound routes are available:

| Route | Authentication |
| --- | --- |
| POST /webhooks/jivo | X-Webhook-Secret header for an authenticated gateway |
| POST /webhooks/jivo/{channel_token} | Configured channel token for direct Bot API callbacks |

The path-based route is provided because the [Jivo Bot API](https://www.jivochat.com/docs/bot/) uses channel-token callback URLs. Disable raw access logging or redact those paths at the gateway.

Text client messages are accepted; other event/message types are acknowledged without generation. A completed event is not generated or delivered again by this process. When delivery fails after generation, the stored response remains ready for a replay of the same event. Replies use a stable event-derived identifier and include Markdown content, a plain-text fallback and a Unix timestamp.

This is replay-aware, at-least-once delivery behavior, not a universal exactly-once guarantee. A remote endpoint may accept a request before a connection fails; its own duplicate handling matters. A process crash while an event is marked processing requires operator recovery. The implementation is documented for one worker; in-process locks are not distributed locks.

## Consented lead capture

POST /leads accepts lead_id, client_id, name, phone and consent. consent must be the boolean true. The service saves the request locally before attempting optional forwarding. Reusing the same lead_id and payload does not create a second record. Reusing it with different details returns a conflict.

If no lead destination is configured, the response reports saved=true and forwarded=false. A configured destination receives a stable Idempotency-Key. Optional Telegram notifications contain only the saved reference, not the full contact payload.

Lead delivery is a separate explicit application action. The model cannot silently invent consent or trigger contact transfer merely by generating text.

## Source map

| Path | Responsibility |
| --- | --- |
| main.py | Authenticated chat, lead and webhook API |
| model/models.py | Parallel extraction, routing and synthesis |
| model/agents.py | Validated JSON role calls and request-local messages |
| model/retrieval.py | TF-IDF or FAISS catalog retrieval |
| model/batching.py | Bounded debounce batches and waiter completion |
| model/database.py | SQLite conversations, event ledger and lead state |
| model/service.py | Reply reuse and outbound event processing |
| model/integrations.py | Explicit external destinations and adapters |
| model/catalog_parser.py | Neutral landing-page and UTM parsing |
| bases/database.md | Fictional English catalog |
| tests/ | Concurrency, isolation, consent and retry regressions |

## Verification and remaining work

Tests exercise all-waiter completion, failed-batch cleanup, queue limits, customer isolation, invalid-router handling, reply reuse after delivery failure, webhook authentication, consent and outbound message structure. They block external network connections and use temporary state.

Independent retrieval/answer evaluation, production load tests, distributed coordination, full consent/audit lifecycle and operator recovery tooling remain deployment work. A saved LLM response or working offline adapter is not evidence of sales effectiveness.

Original client documents, vector caches, contacts, fixed routes and credentials are excluded from the release. See [SECURITY.md](SECURITY.md) and the restrictive [LICENSE](LICENSE).

## License

Publicly visible for portfolio inspection. Use, modification, redistribution and deployment require separate permission under [LICENSE](LICENSE). Third-party dependency licenses continue to apply.
