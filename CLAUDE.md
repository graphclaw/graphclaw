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


## Development Methodology

Follow these phases in order for every development task, without skipping steps.

### Phase 1 — Orient
1. Read CLAUDE.md, build-plan.md, and the relevant PRD section(s) before touching code.
2. Read existing code in the area you will modify — understand what is already there.
3. For UI work (via cockpit): review the relevant wireframe in wireframes-v2/pages/ before designing.

### Phase 2 — Requirements & Planning
4. Document requirements in a .md file; cross-reference existing PRDs and build-plan.md.
5. Validate completeness: identify edge cases, stress scenarios, and failure modes.
6. Identify gaps and ambiguities — clarify key decisions with the user before proceeding. Do not assume.
7. Update build-plan.md with the planned wave/task before writing any code.

### Phase 3 — Implementation
8. Write code sequentially — do not spawn parallel sub-agents (risk of system instability).
9. Write modular code following project conventions (ABC/Factory/Strategy patterns, plugin architecture).
10. Align all API calls with the backend OpenAPI spec; no invented or assumed endpoints.
11. No stubs or fake interfaces — always use the real backend, MinIO, and GraphClaw database.

### Phase 4 — Testing & Validation
12. Run co-located unit tests after each requirement is implemented; all must pass before moving on.
13. Run actual application tests against the live Docker stack — not just unit tests.
14. For front-end (cockpit): verify the UI in a browser against the wireframe; test the golden path and edge cases.
15. Validate data in MinIO and the GraphClaw database directly.
16. Use the CLI interface to test backend APIs directly.
17. Chat with the main orchestrating agent via CLI chat session to verify agent behavior end-to-end.
18. Log in using the Dev auth account for full authentication flow validation.

### Phase 5 — Commit & Close
19. Run the full quality gate: `ruff check --fix src/ tests/ && ruff format src/ tests/ && pytest tests/` — all must pass.
20. Update build-plan.md and relevant docs to mark the wave/requirement complete.
21. Git commit per requirement and per wave using the format: `feat(wave-N): description`.

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
