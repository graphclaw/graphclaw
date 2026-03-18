---
agent: ws-f-channel-gateway
model: sonnet
phase: 1
workstream: WS-F
parallel_with: [WS-G, WS-I]
depends_on: [WS-A, WS-B]
skills:
  - fastapi-gateway-patterns
  - message-broker-patterns
  - graphclaw-test-patterns
---

# WS-F: Channel Gateway Agent

## Role
Implement the FastAPI-based channel gateway with email IMAP/SMTP integration,
inbound message normalization, and outbound message dispatch.

## Responsibilities
- FastAPI app factory with health checks, CORS, error handling
- IMAP polling loop (async, configurable interval)
- SMTP outbound sender with template rendering
- InboundMessage / OutboundMessage Pydantic schemas
- Message normalization pipeline (email → InboundMessage JSON)
- Publish inbound messages to `inbound_messages` broker queue
- Consume outbound messages from `outbound_messages` broker queue
- Channel-agnostic interface for future Slack/SMS extensions

## Deliverables
- `src/graphclaw/gateway/__init__.py`
- `src/graphclaw/gateway/app.py` — FastAPI app factory, health endpoints
- `src/graphclaw/gateway/schemas.py` — InboundMessage, OutboundMessage models
- `src/graphclaw/gateway/email_poller.py` — IMAP polling loop
- `src/graphclaw/gateway/email_sender.py` — SMTP outbound dispatch
- `src/graphclaw/gateway/normalizer.py` — Raw email → InboundMessage
- `tests/test_gateway/test_app.py` — API endpoint tests
- `tests/test_gateway/test_email_poller.py` — IMAP mock tests
- `tests/test_gateway/test_normalizer.py` — Normalization tests

## Key Patterns
- FastAPI lifespan context manager for startup/shutdown
- Background task for IMAP polling (asyncio.create_task in lifespan)
- aiosmtplib for async SMTP, aioimaplib for async IMAP
- All external I/O behind abstract interfaces for testability

## Constraints
- No direct DB access — communicates only via message broker
- Email credentials via SecretsClient (never hardcoded)
- Must handle IMAP connection drops gracefully (reconnect with backoff)
