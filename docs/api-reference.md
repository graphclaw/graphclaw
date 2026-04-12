# API Reference — Channel Gateway

The GraphClaw channel gateway exposes a REST API via FastAPI. Interactive documentation (Swagger UI) is available at `/docs` when the gateway is running.

## Base URL

```
http://localhost:8080
```

Start the gateway:

```bash
docker compose -f docker/docker-compose.yml up -d gateway
# or directly:
python -m graphclaw.gateway.app
```

---

## Endpoints

### GET /health

**Liveness probe.** Always returns 200 while the process is alive.

```http
GET /health
```

**Response `200 OK`:**

```json
{
  "status": "ok",
  "timestamp": "2026-03-19T12:00:00Z"
}
```

---

### GET /health/ready

**Readiness probe.** Checks broker connectivity.

```http
GET /health/ready
```

**Response `200 OK` (ready):**

```json
{
  "status": "ready",
  "broker": "connected"
}
```

**Response `200 OK` (degraded — broker unavailable):**

```json
{
  "status": "degraded",
  "broker": "unavailable"
}
```

---

### POST /api/v1/inbound

**Accept an inbound message from any channel.** Normalizes the payload and publishes it to the `INBOUND_MESSAGES` broker queue for processing by the agent loop.

```http
POST /api/v1/inbound
Content-Type: application/json
```

**Request body:**

```json
{
  "channel": "email",
  "sender_id": "alice@example.com",
  "sender_name": "Alice Smith",
  "subject": "Re: Project Alpha update",
  "body": "Confirmed, moving the deadline to Friday.",
  "attachments": [],
  "received_at": "2026-03-19T10:30:00Z",
  "raw_headers": {
    "message-id": "<abc123@mail.example.com>",
    "in-reply-to": "<xyz@graphclaw.ai>"
  },
  "message_id": "abc123@mail.example.com"
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `channel` | string | Yes | Source channel: `email`, `whatsapp`, `telegram`, etc. |
| `sender_id` | string | Yes | Channel-specific sender address or ID |
| `sender_name` | string | No | Human-readable sender name |
| `subject` | string | No | Message subject (email) or thread title |
| `body` | string | Yes | Plain text message content |
| `attachments` | array | No | List of `{filename, content_type, size, s3_key}` |
| `received_at` | ISO 8601 | Yes | When the message was received |
| `raw_headers` | object | No | Channel-specific metadata |
| `message_id` | string | Yes | Channel-native message ID (used for deduplication) |

**Response `202 Accepted`:**

```json
{
  "status": "queued",
  "message_id": "abc123@mail.example.com",
  "queue": "INBOUND_MESSAGES"
}
```

**Response `422 Unprocessable Entity`:** Validation error (missing required fields).

**Response `503 Service Unavailable`:** Broker unavailable; message not queued.

---

### POST /api/v1/trigger

**On-demand trigger for ad-hoc agent activations.** Used by external systems or the CLI to invoke a specific trigger type immediately.

```http
POST /api/v1/trigger
Content-Type: application/json
```

**Request body:**

```json
{
  "trigger_type": "ON_DEMAND",
  "task_id": "task-abc123",
  "user_id": "user-xyz",
  "reason": "Manual override requested by user",
  "payload": {}
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trigger_type` | string | Yes | `ON_DEMAND`, `FOLLOWUP`, `SCHEDULED_BRIEFING`, `GRAPH_STATE_CHANGE` |
| `task_id` | string | No | Target task ID (for task-scoped triggers) |
| `user_id` | string | No | User context for the trigger |
| `reason` | string | No | Human-readable reason for the trigger |
| `payload` | object | No | Additional context passed to the agent |

**Response `202 Accepted`:**

```json
{
  "status": "queued",
  "trigger_type": "ON_DEMAND",
  "task_id": "task-abc123"
}
```

---

## Authentication

The cockpit API (`/app/v1/...`) requires a valid Bearer JWT token on every request.

**Flow:**
1. `GET /auth/login` — redirects to the configured OAuth 2.0 provider (Google, GitHub, or Microsoft) using PKCE
2. `GET /auth/callback` — exchanges the auth code for an access token + refresh token; returns a signed JWT
3. Include the JWT as `Authorization: Bearer {token}` on all `/app/v1/` requests
4. `POST /auth/refresh` — exchange a refresh token for a new access token (token rotation is enforced)
5. `POST /auth/logout` — revokes the refresh token

The gateway inbound/outbound endpoints (`/api/v1/inbound`, `/api/v1/outbound`) use channel-specific authentication (HMAC signatures, webhook secrets) rather than user JWTs.

For local development, set `AUTH_BYPASS_SECRET` in your environment to skip OAuth and issue tokens directly. Never use this in production.

---

## Swagger UI

When the gateway is running, the full interactive API documentation is available at:

```
http://localhost:8080/docs
```

OpenAPI JSON schema:

```
http://localhost:8080/openapi.json
```

---

## Channel-Specific Webhooks

Each channel adapter may also expose channel-specific webhook endpoints for receiving messages directly from provider APIs (e.g., WhatsApp webhook, Telegram webhook). These are registered by the `ChannelAdapter` implementation and documented per-channel.

| Channel | Webhook path | Auth method | Status |
|---------|-------------|-------------|--------|
| Email | N/A (uses IMAP polling) | — | Implemented |
| WhatsApp | `/webhooks/whatsapp` | HMAC-SHA256 | Implemented |
| Telegram | `/webhooks/telegram` | Secret token header | Implemented |
| Slack | `/webhooks/slack` | HMAC-SHA256 | Implemented |
| Teams | `/webhooks/teams` | HMAC-SHA256 | Implemented |
