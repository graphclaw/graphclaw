# GraphClaw — Plugin Architecture

GraphClaw is built around a **4-layer plugin architecture** where every infrastructure concern is defined as an Abstract Base Class (ABC) and implemented by swappable concrete backends. This makes it straightforward to add new database engines, LLM providers, channel adapters, and infrastructure backends without touching any business logic.

## Design Pattern

Each layer follows the same three-part structure:

```
ABC (base.py)          — defines the contract (abstract methods)
Factory (factory.py)   — creates the right backend from config/env vars
Concrete impl          — implements the ABC for a specific technology
```

## The 4 Layers

### 1. Database Layer — `src/graphclaw/db/`

**Contract:** `GraphStore` + `GraphQueryEngine` ABCs in `src/graphclaw/db/base.py`

| ABC | Methods |
|-----|---------|
| `GraphStore` | `upsert_node`, `get_node`, `delete_node`, `upsert_edge`, `get_edges`, `list_nodes`, `traverse`, `close` |
| `GraphQueryEngine` | `critical_path_nodes`, `dependency_chain`, `blocked_nodes`, `recently_completed`, `goal_tasks`, `scoring_context`, `find_similar_tasks` |

**Factory:** `create_graph_store(backend="age", pool=...) -> GraphStore`

**Backends:**
| Backend | Path | Description |
|---------|------|-------------|
| `age` | `src/graphclaw/db/age/` | PostgreSQL + Apache AGE (Cypher) + pgvector |
| _(future)_ | `src/graphclaw/db/neo4j/` | Native Neo4j driver |
| _(future)_ | `src/graphclaw/db/neptune/` | AWS Neptune (Gremlin) |

**Backward compat:** `GraphRepository` is aliased to `AgeGraphStore` in `src/graphclaw/db/_compat.py`.

---

### 2. Gateway Layer — `src/graphclaw/gateway/`

**Contract:** `ChannelAdapter` ABC in `src/graphclaw/gateway/channel_base.py`

| Method | Purpose |
|--------|---------|
| `channel_id` (property) | Unique string identifier for this channel |
| `start_polling()` | Begin receiving messages (e.g., IMAP polling) |
| `stop_polling()` | Graceful shutdown |
| `send_message(to, body, attachments)` | Send a message via this channel |
| `normalize(raw)` | Convert channel-specific payload to `InboundMessage` |

**Registry:** `ChannelRegistry` in `src/graphclaw/gateway/channel_registry.py` uses `importlib` to discover and load channel plugins at runtime.

**Channels:**
| Channel | Path | Protocol |
|---------|------|----------|
| `email` | `src/graphclaw/gateway/channels/email/` | IMAP polling + SMTP send |
| `whatsapp` | `src/graphclaw/gateway/channels/whatsapp/` | Webhook + HMAC-SHA256 |
| `telegram` | `src/graphclaw/gateway/channels/telegram/` | Webhook + bot token header |
| `slack` | `src/graphclaw/gateway/channels/slack/` | OAuth 2.0 + Events API |
| `teams` | `src/graphclaw/gateway/channels/teams/` | OAuth 2.0 + Activity Feed |

All five channels are fully implemented. WhatsApp and Telegram are webhook-based; Slack and Teams use OAuth 2.0 app installations.

---

### 3. LLM Provider Layer — `src/graphclaw/llm/`

**Contract:** `LLMClient` ABC in `src/graphclaw/llm/base.py`

| Method | Signature |
|--------|-----------|
| `complete` | `async (messages, *, model, max_tokens, temperature, tools) -> LLMResponse` |
| `stream` | `async (messages, **kwargs) -> AsyncIterator[LLMStreamChunk]` |
| `count_tokens` | `async (messages, *, model) -> int` |
| `close` | `async () -> None` |

**Shared data models** (all providers use these):
- `LLMMessage(role, content, tool_call_id, tool_calls)`
- `LLMResponse(content, model, tokens_used, prompt_tokens, completion_tokens, cost_usd, tool_calls, stop_reason)`
- `ToolDefinition(name, description, parameters)` — JSON Schema
- `ToolCall(id, name, arguments)`
- `LLMStreamChunk(content_delta, is_final, accumulated)`

**Factory:** `create_llm_client(provider="litellm", **kwargs) -> LLMClient`

**Providers:**
| Provider | Path | SDK |
|----------|------|-----|
| `litellm` | `src/graphclaw/llm/litellm/` | `litellm.acompletion()` (default, 100+ models) |
| `anthropic` | `src/graphclaw/llm/anthropic/` | `anthropic.AsyncAnthropic` |
| `openai` | `src/graphclaw/llm/openai/` | `openai.AsyncOpenAI` |

**Backward compat:** `LLMRouter` in `src/graphclaw/skills/llm_router.py` is a thin adapter over `LLMClient`. Existing code using `LLMRouter` continues to work unchanged.

---

### 4. Infrastructure Layer — `src/graphclaw/infra/`

**Contracts:** Three ABCs for storage, messaging, and secrets.

| ABC | File | Backends |
|-----|------|----------|
| `StorageClient` | `storage.py` | `S3StorageClient` — serves both MinIO (local dev) and AWS S3 (production) via `endpoint_url`; use `StorageConfig.from_env()` to construct transparently |
| `MessageBroker` | `broker.py` | `RedisMessageBroker` — single implementation used in both local and production (ElastiCache); `SQSBroker` is planned for the scale phase, see [`docs/future-phases.md`](../future-phases.md) |
| `SecretsClient` | `secrets.py` | `EnvFileClient` (local dev), `AWSSecretsClient` (production), `HashiCorpVaultClient` (enterprise) — all three fully implemented |

**Path registry:** `StoragePaths` in `storage.py` — static class that is the single source of truth for all `{user_id}/` prefixed object paths. No code may construct storage path strings by hand; always call `StoragePaths.<method>()`. See [`docs/architecture/08-object-storage-model.md`](architecture/08-object-storage-model.md).

**Config factory:** `StorageConfig.from_env()` reads `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, and `STORAGE_REGION` from environment and returns a configured `StorageConfig`. Call `.create_client()` on the result to get a ready-to-use `S3StorageClient`. This makes MinIO ↔ S3 selection transparent — callers never reference `S3StorageClient` directly.

**Logging:** `AsyncLogger` in `infra/logger.py` — structured JSON, session_id tracing, async buffered writes.

**Message broker design note:** The `MessageBroker` ABC uses Redis Lists (`LPUSH` / `BRPOP`) rather than Redis Pub/Sub. Lists provide durable queue semantics (messages persist until consumed) whereas Pub/Sub is ephemeral (messages lost if no subscriber is connected). Redis Pub/Sub is used separately for real-time SSE event streaming to the cockpit UI (`graphclaw:events:{user_id}` per-user channels) — a different system from the application queues.

**Queue names** (defined in `infra/broker.py` as `QueueNames`):

| Constant | Queue name | Role |
|----------|-----------|------|
| `INBOUND_MESSAGES` | `inbound_messages` | All channels → inbound processor |
| `TRIGGER_EVENTS` | `trigger_events` | Trigger engine → agent orchestrator |
| `SKILL_JOBS` | `skill_jobs` | API → skill worker pool (consumer planned) |
| `STATUS_UPDATES` | `status_updates` | Inbound processor → state machine (consumer planned) |
| `OUTBOUND_MESSAGES` | `outbound_messages` | Agent/API → channel senders |

> **Note:** `BrokerConfig.backend` accepts `"redis"` or `"sqs"` but no factory reads it yet — `RedisMessageBroker` is always instantiated directly. Wiring this field to a factory is part of the SQS implementation plan in `docs/future-phases.md`.

---

## MCP Server Architecture (Design + Implementation)

GraphClaw's MCP subsystem is implemented across API routes and the `mcp/` package, with strict trust-tier enforcement and per-user registry isolation.

### Core Components

| Component | File | Responsibility |
|----------|------|----------------|
| `MCPRegistry` | `src/graphclaw/mcp/registry.py` | Persist/list/update server registrations per user |
| `MCPClient` | `src/graphclaw/mcp/client.py` | Connect/list/call MCP tools across http/sse/stdio |
| `GatedApprovalService` | `src/graphclaw/mcp/approval.py` | Human approval workflow for GATED calls |
| `OfficialMCPRegistry` | `src/graphclaw/mcp/official_registry.py` | Search official registry index |
| MCP API routes | `src/graphclaw/api/mcp_registry.py` | `/app/v1/mcp-servers*` and `/app/v1/mcp-approvals` |

### Persistence Model

Registered servers are represented by `MCPServerNode` schema but persisted as object-storage JSON docs (not graph vertices in current runtime path):

- Object key: `{user_id}/mcp/servers/{server_id}.json`
- Fields include transport config (`endpoint_url` or `command`), `trust_tier`, `scope`, and `enabled`
- Optional `secret_ref` allows credentials lifecycle cleanup during deregistration

### Trust-Tier Execution Policy

`MCPClient.call_tool()` applies runtime policy before any tool executes:

- `AUTO` - execute immediately
- `GATED` - create APPROVAL task via `GatedApprovalService`, wait for decision
- `BLOCKED` - reject with `MCPToolBlockedError`

This keeps policy enforcement centralized in one code path instead of duplicating checks per route or per caller.

### API Surface

`/app/v1/mcp-servers` provides CRUD + discovery + tool introspection:

- `GET /mcp-servers`
- `POST /mcp-servers`
- `GET /mcp-servers/search`
- `GET/PATCH/DELETE /mcp-servers/{server_id}`
- `GET /mcp-servers/{server_id}/tools`
- `GET /mcp-approvals`

### Failure and Degradation Strategy

- Official registry search failures return empty lists, not API crashes
- Tool discovery failures return empty lists with warnings
- MCP SDK import is lazy and produces explicit install guidance when missing

---

## How to Add a New Backend

### New LLM Provider

See [`docs/llm-providers.md`](llm-providers.md) for the full guide. Summary:

1. Create `src/graphclaw/llm/<provider>/client.py`
2. Implement `LLMClient` ABC (4 methods)
3. Add the provider name to `src/graphclaw/llm/factory.py`
4. Add tests in `tests/test_llm/test_<provider>.py`

### New Gateway Channel

See [`docs/channels.md`](channels.md) for the full guide. Summary:

1. Create `src/graphclaw/gateway/channels/<name>/adapter.py`
2. Implement `ChannelAdapter` ABC
3. The `ChannelRegistry` will discover it automatically via `importlib`
4. Add tests in `tests/test_gateway/test_<name>.py`

### New Database Backend

See [`docs/db-backends.md`](db-backends.md) for the full guide. Summary:

1. Create `src/graphclaw/db/<name>/store.py`
2. Implement `GraphStore` + `GraphQueryEngine` ABCs
3. Register in `src/graphclaw/db/factory.py`
4. Add tests in `tests/test_db/test_<name>.py`

---

## Dependency Injection

### Gateway / Agent Loop (constructor injection)

All ABCs flow into the agent loop and gateway services via constructor injection. No global state, no service locators.

```python
# Example: wiring the application
pool = await create_pool(DATABASE_URL)
graph_store = create_graph_store(backend="age", pool=pool)
llm_client = create_llm_client(provider="anthropic", api_key=ANTHROPIC_API_KEY)

agent_loop = AgentLoop(
    graph_repo=graph_store,
    scoring_engine=ScoringEngine(),
    state_machine=StateMachine(),
    llm_client=llm_client,
)
```

### Cockpit API (FastAPI dependency injection)

The cockpit API layer (`src/graphclaw/api/`) uses **FastAPI's `Depends()` system** via `src/graphclaw/api/deps.py`. Each HTTP request receives its own scoped instances — no module-level singletons in the API layer.

```python
# src/graphclaw/api/deps.py — key dependency providers
async def get_storage_client() -> StorageClient: ...      # S3StorageClient per request
async def get_skill_registry_service() -> SkillRegistryService: ...
async def require_auth(token: str = Security(...)) -> str: ...  # returns user_id

# Type aliases used in route signatures
CurrentUserDep = Annotated[str, Depends(require_auth)]
StorageClientDep = Annotated[StorageClient, Depends(get_storage_client)]

# Example route
@router.get("/intelligence/agents/{agent_id}/profile")
async def get_profile(
    agent_id: str,
    user_id: CurrentUserDep,       # resolved from Bearer token
    storage_client: StorageClientDep,  # resolved fresh per request
) -> AgentProfileResponse: ...
```

**Benefits:**
- Every API handler is independently testable by overriding dependencies in `app.dependency_overrides`
- `StorageClient` is never a module-level singleton — safe for concurrent requests
- Auth is enforced at the dependency level; routes cannot accidentally bypass it

This pattern makes every component independently testable with mock implementations.
