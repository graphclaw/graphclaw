# Changelog

All notable changes to GraphClaw are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### In Progress (Phase 4 — Application API Layer)
- 21 stub endpoints in `/app/v1/` router need real implementations (settings, approvals, skills, MCP CRUD)
- 8 new cockpit backend API modules: `api/graph.py`, `api/scoring.py`, `api/state.py`, `api/events.py`, `api/chat.py`, `api/config.py`, `api/secrets.py`, `api/agent.py`
- SSE event stream via Redis pub/sub
- Chat API + WebSocket for cockpit interface

---

## [0.4.0] — 2026-04-10

### Phase 4 (Partial): Agent Interop, MCP, Connectors & Skill Registry

#### Added

**A2A REST API (WS-P4-A)**
- `src/graphclaw/a2a/` — per-agent API keys (`wg_agent_` prefix, SHA-256 hash), key lifecycle (register/rotate/revoke)
- `a2a/key_manager.py` — key generation, hashing, rotation
- `a2a/middleware.py` — API key authentication middleware
- `a2a/models.py` — A2AAgent, A2AKey Pydantic models
- `a2a/routes.py` — `POST /api/v1/a2a/agents`, `GET /api/v1/a2a/agents`, `DELETE`, `POST /api/v1/task-update`

**Connectors ABC + Adapters (WS-P4-B)**
- `src/graphclaw/connectors/` — CalendarConnector + ImportConnector ABCs with factory
- `connectors/calendar/google/` — Google Calendar adapter (read/write events, free/busy)
- `connectors/calendar/outlook/` — Outlook Calendar adapter (MS Graph API)
- `connectors/import_/jira/` — Jira issue importer → TaskNode
- `connectors/import_/asana/` — Asana task importer → TaskNode
- `connectors/import_/notion/` — Notion page importer → TaskNode

**Skill Registry v2 (WS-P4-C)**
- `skills/registry.py` — remote GitHub + marketplace.json sources, install/uninstall/search
- `skills/registry_models.py` — SkillRegistryEntry, SkillSource, version pinning models

**MCP Server Registry + Client Runtime (WS-P4-D)**
- `src/graphclaw/mcp/` — MCPServerNode CRUD, official registry search, trust tier enforcement
- `mcp/registry.py` — per-user MCPServerNode store with AUTO/GATED/BLOCKED trust tiers
- `mcp/client.py` — MCP client runtime (SSE/HTTP transports)
- `mcp/official_registry.py` — search against registry.modelcontextprotocol.io
- `mcp/models.py` — MCPServerNode, MCPToolCall, TrustTier Pydantic models

**Pre-Built MCP Adapters + GATED Approval (WS-P4-E)**
- `mcp/adapters/google_calendar/` — Google Calendar MCP adapter
- `mcp/adapters/github/` — GitHub MCP adapter
- `mcp/adapters/slack/` — Slack MCP adapter
- `mcp/approval.py` — GATED workflow: creates APPROVAL TaskNode, notifies via channel, resolves on reply

**Application API Layer / Stub Router (WS-P4-F partial)**
- `src/graphclaw/api/router.py` — `/app/v1/` FastAPI router merged into gateway
- `api/settings.py` — **stub** GET/PATCH /app/v1/settings, channel status
- `api/approvals.py` — **stub** GET /app/v1/approvals, POST approve/deny
- `api/skill_registry.py` — **stub** skills CRUD + search + sources
- `api/mcp_registry.py` — **stub** MCP server CRUD + search
- `api/a2a_keys.py` — **real** A2A key management UI endpoints
- `api/compliance.py` — **real** GDPR export + erasure endpoints

**Schema Migrations (WS-P4-H)**
- `src/graphclaw/migrations/` — forward-only, non-destructive, version-stamped migration runner
- `migrations/runner.py` — idempotent migration executor
- `migrations/catalogue.py` — migration catalogue with schema_version tracking
- `migrations/models.py` — MigrationRecord model

#### Tests
- 1333 unit tests passing (0 failures); 15 DB integration tests require live Postgres+AGE
- New test modules: `test_a2a/`, `test_api/`, `test_auth/`, `test_compliance/`, `test_connectors/`, `test_mcp/`, `test_migrations/`

---

## [0.3.0] — 2026-03-28

### Phase 3: Multi-User + Delegation + Security

#### Added

**Auth Stack (WS-P3-A)**
- `src/graphclaw/auth/` — complete OAuth 2.0 + PKCE authentication stack
- `auth/oauth.py` — Google, Microsoft, GitHub IdP flows with PKCE
- `auth/jwt.py` — RS256 platform JWT issuance (15-min expiry), refresh token rotation, Redis jti revocation
- `auth/middleware.py` — JWT bearer token middleware for FastAPI
- `auth/routes.py` — `/auth/login`, `/auth/callback`, `/auth/refresh`, `/auth/logout`, `/auth/me`

**User Provisioning (WS-P3-B)**
- `auth/provisioning.py` — atomic UserNode + S3 prefix + IAM role + SQS queue creation with rollback on failure

**Delegation + Escalation (WS-P3-C)**
- `agent/delegation.py` — cross-user task delegation flow for Delegated TaskNodes
- `agent/escalation.py` — approval task escalation paths with timeout handling

**BYOK LLM Keys (WS-P3-D)**
- `infra/byok.py` — per-user LLM API key storage via SecretsClient; key never stored in DB or logs

**Compliance (WS-P3-E)**
- `src/graphclaw/compliance/` — full GDPR compliance pipeline
- `compliance/gdpr.py` — right-to-erasure with PII anonymization
- `compliance/audit.py` — immutable audit log writer and reader
- `compliance/export.py` — user data export (structured JSON)
- `compliance/models.py` — ErasureRequest, AuditEntry, DataExport Pydantic models

**Visibility Grants (WS-P3-F)**
- `models/nodes.py` — VisibilityGrant model; node-level access control for multi-user graphs

**Scoring Weights Learning (WS-P3-G)**
- `models/scoring_weights.py` — per-user W1–W7 weights with exponential moving average update on human override signals

#### Tests
- Phase 3 test suites: `test_auth/`, `test_compliance/`, `test_agent/` (delegation + escalation)

---

## [0.2.0] — 2026-03-19

---

## [0.2.0] — 2026-03-19

### Phase 1: Single-User System + Plugin Architecture

#### Added

**Channel Gateway (Phase 1 WS-F)**
- `src/graphclaw/gateway/` — FastAPI gateway application factory (`create_app`)
- `ChannelAdapter` ABC (`channel_base.py`) — pluggable channel contract
- `ChannelRegistry` (`channel_registry.py`) — importlib-based plugin discovery
- Email channel plugin (`channels/email/`) — IMAP polling + SMTP send
- Gateway endpoints: `GET /health`, `GET /health/ready`, `POST /api/v1/inbound`, `POST /api/v1/trigger`
- Swagger UI at `/docs`
- Docker gateway service in `docker/docker-compose.yml`

**Trigger Engine (Phase 1 WS-G)**
- `TriggerEngine` — scheduled, inbound, on-demand, and follow-up triggers
- Follow-up timing model with configurable delay buckets
- Trigger dispatch to message broker

**Skill Agent Runtime (Phase 1 WS-H)**
- `SKILL.md` format parser (`skills/parser.py`) — YAML frontmatter + markdown body
- `SkillWorker` async worker pool with priority queue dispatch
- `HeartbeatMonitor` — detect stalled skill agents via `status.md` polling
- `LLMRouter` — multi-provider LLM calls via LiteLLM
- Skill state machine: SPAWNING → RUNNING → COMPLETED / FAILED / TIMED_OUT

**Infrastructure ABCs (Phase 1 WS-I)**
- `StorageClient` ABC (`infra/storage.py`) with `MinIOStorageClient` implementation
- `MessageBroker` ABC (`infra/broker.py`) with `RedisBroker` implementation
- `SecretsClient` ABC (`infra/secrets.py`) with `EnvFileClient` implementation
- `AsyncLogger` (`infra/logger.py`) — structured JSON, session_id tracing, async buffered writes

**Inbound Update Protocol (Phase 1 WS-J)**
- Task resolver: ID regex lookup + pgvector embedding similarity search
- Status signal extractor — parse completion signals from free text
- Inbound processor pipeline

**Briefing (Phase 1 WS-K)**
- `format_briefing()` — human-readable markdown briefing from scoring results
- `AgentLoop` scoring cycle orchestration

**Plugin Architecture Refactoring**
- `GraphStore` + `GraphQueryEngine` ABCs (`db/base.py`)
- `create_graph_store()` factory (`db/factory.py`)
- `AgeGraphStore` — Apache AGE implementation of `GraphStore`
- `AgeGraphQueryEngine` — Apache AGE implementation of `GraphQueryEngine`
- `GraphRepository` backward compat alias (`db/_compat.py`)
- `LLMClient` ABC (`llm/base.py`) with shared data models
- `create_llm_client()` factory (`llm/factory.py`)
- `LiteLLMLLMClient`, `AnthropicLLMClient`, `OpenAILLMClient` provider implementations
- `LLMRouter` refactored to delegate to `LLMClient` (backward compatible)

**Open-Source Readiness**
- Apache 2.0 license (`LICENSE`)
- `CONTRIBUTING.md`
- `pyproject.toml` updated: version 0.2.0, classifiers, license, URLs, optional OpenAI dep
- Documentation: `docs/architecture.md`, `docs/llm-providers.md`, `docs/channels.md`, `docs/db-backends.md`, `docs/api-reference.md`, `docs/skills-and-agents-roadmap.md`

#### Changed
- All consumers migrated from `GraphRepository` to `GraphStore` ABC (scoring engine, state machine, agent loop, CLI)
- `README.md` rewritten for open-source audience with plugin architecture section
- `CLAUDE.md` updated: Phase 1 complete, plugin architecture conventions, LLM abstraction pattern

#### Tests
- 485+ unit tests passing (0 failures, 0 errors)
- New: `tests/test_llm/` — LLM abstraction layer tests (ABC, factory, 3 providers, router compat)

---

## [0.1.0] — 2026-03-17

### Phase 0: Core Loop Proof

#### Added

**Graph Schema**
- 17 node types: Atomic, Composite, Delegated, Follow-up, Approval, Project, Goal, Resource, Skill, Constraint, Stakeholder, User, Trigger, Channel, Organization, Workspace, AgentState
- 8 edge types: DEPENDS_ON, BLOCKS, ASSIGNED_TO, PART_OF, INFLUENCES, MEMBER_OF, HAS_CHANNEL, DELEGATED_TO
- Apache AGE schema DDL (`scripts/init-db.sql`)
- pgvector embedding table

**7-Factor Scoring Engine**
- W1 Timeline Urgency (0.25) — days to deadline, effort slack
- W2 Dependency Weight (0.20) — transitive downstream dependents
- W3 Critical Path (0.20) — Cypher critical path detection
- W4 Blocker Score (0.15) — hard/soft blocker elevation
- W5 Human Override (0.10) — prioritize / deprioritize / snooze
- W6 Resource Risk (0.05) — reliability, load, risk signals
- W7 Constraint Pressure (0.05) — budget/time/resource proximity
- Post-multipliers: P1 goal on critical path = 1.5×
- Chain topology: sequential suppression, urgency rollup to chain head

**State Machine**
- 10 task states with guarded transitions
- INACTIVE_PENDING activation via CASCADE / HUMAN / SYSTEM
- Composite completion cascade
- Approval task enforcement (human-only completion)
- History recording on every transition

**Agent Reasoning Loop**
- `AgentLoop`: fetch → score → build action queue
- `format_briefing()` — human-readable briefing

**CLI**
- `graphclaw task list/show/create/transition`
- `graphclaw goal list/show`
- `graphclaw graph stats/query`
- `graphclaw agent run/score/briefing`

**Database**
- `GraphRepository` — node/edge CRUD via Apache AGE Cypher
- Async psycopg pool with AGE session setup
- Cypher query functions: critical path, dependency chain, scoring context

**Infrastructure**
- Docker Compose local dev stack (Postgres + AGE + pgvector + app)
- Seed data (`scripts/seed-data.sql`) — 6 tasks with dependencies

#### Tests
- 211 unit tests passing (0 failures)
- Models: 57 tests, State: 41 tests, Scoring: 52 tests, Agent: 22 tests, CLI: 25 tests, DB integration: 15 tests

---

[Unreleased]: https://github.com/abhishekgupta-myrepo/graphclaw/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/abhishekgupta-myrepo/graphclaw/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/abhishekgupta-myrepo/graphclaw/releases/tag/v0.1.0
