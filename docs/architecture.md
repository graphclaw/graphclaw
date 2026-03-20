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
| _(Phase 2)_ | `src/graphclaw/gateway/channels/whatsapp/` | Webhook + HMAC |
| _(Phase 2)_ | `src/graphclaw/gateway/channels/telegram/` | Webhook + bot token |
| _(Phase 5)_ | `src/graphclaw/gateway/channels/slack/` | OAuth 2.0 + Events API |
| _(Phase 5)_ | `src/graphclaw/gateway/channels/teams/` | OAuth 2.0 + Activity Feed |

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
| `StorageClient` | `storage.py` | `MinIOStorageClient` (local), `S3StorageClient` (prod) |
| `MessageBroker` | `broker.py` | `RedisBroker` (local), `SQSBroker` (prod) |
| `SecretsClient` | `secrets.py` | `EnvFileClient` (local), `AWSSecretsClient` (Phase 3), `HashiCorpVaultClient` (Phase 3) |

**Logging:** `AsyncLogger` in `infra/logger.py` — structured JSON, session_id tracing, async buffered writes.

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

All ABCs flow into the application via constructor injection. No global state, no service locators.

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

This pattern makes every component independently testable with mock implementations.
