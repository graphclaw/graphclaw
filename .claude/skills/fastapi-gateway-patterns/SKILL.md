---
name: fastapi-gateway-patterns
description: >
  FastAPI patterns for GraphClaw channel gateway — email IMAP/SMTP integration,
  inbound message normalization, trigger dispatching, health checks, and structured
  error responses. Use when implementing API endpoints, channel handlers, webhook
  receivers, or gateway middleware. Triggers on: "gateway", "FastAPI", "endpoint",
  "channel handler", "IMAP", "SMTP", "email polling".
---

# FastAPI Gateway Patterns

## Gateway Architecture

```
IMAP Poll / Webhook → FastAPI app → Normalize → Inbound Update Protocol → Agent
                                  → Trigger Engine → Outbound Dispatcher → SMTP
```

## App Structure

```python
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB pool, start IMAP poller, connect Redis
    pool = await create_pool(config.database.dsn)
    app.state.pool = pool
    app.state.broker = await create_broker()
    poller_task = asyncio.create_task(imap_poll_loop(app.state))
    yield
    # Shutdown: cancel poller, close pool
    poller_task.cancel()
    await pool.close()

app = FastAPI(title="GraphClaw Gateway", lifespan=lifespan)
```

## Email Channel Pattern

### IMAP Polling Loop
```python
async def imap_poll_loop(state, interval_seconds: int = 30):
    """Poll IMAP inbox for new messages, normalize, dispatch."""
    while True:
        try:
            messages = await fetch_unseen_emails(state.imap_config)
            for msg in messages:
                inbound = normalize_email(msg)
                await state.broker.publish("inbound_messages", inbound.model_dump_json())
        except Exception as exc:
            logger.warning("imap_poll_error", extra={"error": str(exc)})
        await asyncio.sleep(interval_seconds)
```

### SMTP Outbound
```python
async def send_email(to: str, subject: str, body: str, config: SMTPConfig) -> bool:
    """Send via SMTP with retry. Returns True on success."""
    # Use aiosmtplib for async SMTP
    # Retry up to 3 times with exponential backoff
```

## Inbound Message Schema
```python
class InboundMessage(BaseModel):
    message_id: str
    channel: Literal["email", "whatsapp", "telegram"]
    sender_id: str
    content: str
    content_type: Literal["text", "rich_text", "attachment"]
    received_at: datetime
    metadata: dict = {}
    session_id: str  # SES-uuid4, generated at gateway entry
```

## Health Check Endpoint
```python
@app.get("/health")
async def health(request: Request):
    pool_ok = request.app.state.pool.get_stats()["pool_available"] > 0
    return {"status": "healthy" if pool_ok else "degraded", "db_pool": pool_ok}
```

## Error Response Convention
```python
class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    # NEVER include: stack traces, internal IDs, DB connection strings
```

## Key Rules
- All endpoints return Pydantic models (not raw dicts)
- Use dependency injection for pool/repo access
- session_id propagated from gateway entry through all downstream calls
- IMAP credentials loaded via SecretsClient, never hardcoded
- Rate limit: 100 req/min per sender (foundation for Section 31.7)
