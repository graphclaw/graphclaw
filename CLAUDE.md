# GraphClaw — Task Graph Management System

## Project Context
Graph-based task orchestration system where an AI agent manages tasks for humans and other agents via a property graph.
- PRD: `docs/graphclaw-requirements.md` (v1.1, 8500+ lines)
- Review notes: `docs/graphclaw-review-notes.md` (28 issues across 6 categories)
- Build plan: `build-plan.md` (6 phases, 48 weeks)
- Domain: graphclaw.ai
- GitHub: https://github.com/abhishekgupta-myrepo/graphclaw
- License: Apache 2.0

## Build System
This project is built entirely using Claude Code multi-agent system.
- Opus: Architecture decisions, planning, complex reasoning, code review
- Sonnet: Implementation, code generation, testing, refactoring
- Haiku: Quick lookups, formatting, simple edits

## Tech Stack
- Python 3.12+
- Postgres + Apache AGE (graph) + pgvector (embeddings)
- FastAPI (API server + channel gateway)
- Anthropic SDK (orchestrating agent LLM calls)
- OpenAI SDK (optional, via LLMClient ABC)
- LiteLLM (multi-provider skill agent calls, default LLM backend)
- Pydantic (schema validation)
- Docker Compose (local dev)
- MinIO (local S3-compatible storage)
- Redis (caching, message broker for local dev)

## Sub-Agent Orchestration Layer (Phase 5)
The main `AgentLoop` orchestrates work by delegating tasks to sub-agents running in parallel background. Key components:

| Component | File | Purpose |
|-----------|------|---------|
| `SubAgentRunner` | `agent/sub_agent_runner.py` | Mini-AgentLoop: reads context from MinIO, calls LLM with `invoke_skill`/`call_mcp_tool`, emits typed events to `AGENT_UPDATES` |
| `SubAgentPool` | `agent/sub_agent_pool.py` | Bounded pool of `SubAgentRunner` instances (max `GRAPHCLAW_MAX_CONCURRENT_AGENTS`); fan-in via `BatchCoordinator` |
| `AgentDispatchPlanner` | `agent/dispatch_planner.py` | Topological sort over task `DEPENDS_ON` edges → ordered parallel dispatch tiers |
| `AgentHealthMonitor` | `agent/health_monitor.py` | Tracks sub-agent heartbeats; marks task BLOCKED + escalates on timeout |

**New broker queues:**

| Queue | Publisher | Consumer |
|-------|-----------|----------|
| `agent_jobs` | `AgentLoop._tool_delegate_to_agent()` | `SubAgentPool` |
| `agent_updates` | `SubAgentRunner` | `AgentEventConsumer._consume_agent_updates_loop()` |

**New config env vars:**
- `GRAPHCLAW_MAX_CONCURRENT_AGENTS` — max parallel sub-agents (default 4)
- `GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE` — dedicated skill worker pool for sub-agents (default 4)
- `GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS` — heartbeat emit interval (default 60)
- `GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS` — timeout before BLOCKED (default 300)

**Design constraints:**
- Delegation is flat (depth = 2): sub-agents cannot call `delegate_to_agent`
- Sub-agents use a dedicated `WorkerPool` separate from the orchestrator's pool
- On heartbeat timeout: mark task BLOCKED + escalate (no retry — prevents duplicate MCP writes)

## Plugin Architecture (4 layers)
All four infrastructure layers use ABC + Factory + Strategy pattern so backends are swappable:

| Layer | ABC | Factory | Current Backends |
|-------|-----|---------|-----------------|
| Database | `GraphStore`, `GraphQueryEngine` | `create_graph_store()` | `age/` (Postgres+AGE) |
| Gateway | `ChannelAdapter` | `build_registry()` | `channels/email/` |
| LLM | `LLMClient` | `create_llm_client()` | `litellm/`, `anthropic/`, `openai/` |
| Infra | `StorageClient`, `MessageBroker`, `SecretsClient` | constructor DI | S3/MinIO, Redis, env_file |

To add a new backend: implement the ABC, drop it in the subfolder, register in the factory. See `docs/architecture.md`.

## Conventions
- All agent state files use markdown with YAML frontmatter, `schema_version` field in main.md
- Storage abstraction: StorageClient interface (S3/MinIO/GCS/Azure Blob)
- Message broker abstraction: MessageBroker interface (SQS/BullMQ/Pub/Sub)
- Secrets abstraction: SecretsClient interface (AWS SM/Vault/Azure KV/GCP SM/env_file)
- **LLM abstraction**: `LLMClient` ABC in `src/graphclaw/llm/base.py`; use `create_llm_client(provider, **kwargs)` from `src/graphclaw/llm/factory.py`; never call SDK directly
- Node schemas validated via Pydantic models
- Graph queries via Apache AGE Cypher syntax
- Auth: OAuth 2.0 (Google/Microsoft/GitHub) + platform JWT (RS256, 15min expiry)
- Logging: Structured JSON, async buffered writes, session_id distributed tracing
- IAM: One role per container, least-privilege, user-scoped S3 prefix conditions
- Tests: pytest, run with `pytest tests/`
- **Linting:** `ruff check src/ tests/` — must pass before any commit
- **Formatting:** `ruff format src/ tests/` — must be applied before any commit
- Run both together before committing: `ruff check --fix src/ tests/ && ruff format src/ tests/`
- CI enforces both; failing either blocks the build
- Local dev: `docker compose up` (SECRETS_BACKEND=env_file)

## PRD Coverage (v1.1)
- Sections 1-30: Core system (graph model, agents, channels, skills, multi-user)
- Section 31: Security, Identity & Secrets (OAuth, IAM roles, attack surface assessment)
- Section 32: Observability & Operations (structured logging, tracing, cost monitoring, alerting, backups, rolling deployments, schema migration)
- Section 33: 58 Design Principles (14 new in v1.1: security, observability, deployment)

## Current Phase
Phases 0–4 complete. Phase 4.5 (Intelligence Layer) complete (1451 tests passing). Phase 5 (Sub-Agent Parallel Orchestration) is active.

**Phase 5 (active):** Sub-agent parallel orchestration. New files: `agent/sub_agent_runner.py`, `agent/sub_agent_pool.py`, `agent/dispatch_planner.py`, `agent/health_monitor.py`. Modified: `agent/loop.py` (`_tool_delegate_to_agent`), `agent/event_consumer.py` (third background task), `infra/broker.py` (new queues), `infra/config.py` (new env vars), `gateway/app.py` (lifespan wiring). See `build-plan.md` Phase 5 section.
