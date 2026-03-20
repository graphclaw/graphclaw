# Changelog

All notable changes to GraphClaw are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned (Phase 2 — Multi-Channel + Organizations)
- WhatsApp Business channel adapter (webhook + HMAC auth)
- Telegram Bot channel adapter (webhook + bot token)
- Organization workspaces with isolation boundaries
- Conversation context cache (Redis)
- Channel switching within active conversations
- Meeting Notes Agent skill

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
