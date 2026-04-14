# GraphClaw — Phased Build Plan

**Project:** Graph-based Task Orchestration System (OpenClaw / GraphClaw)
**PRD:** task-graph-requirements.md (v1.1 — Observability, Operations & Deployment)
**Domain:** graphclaw.ai
**Created:** 2026-03-17

---

## Build System

**The entire build is designed and implemented using Claude Code multi-agent system.**

| Role | Model | Responsibility |
|------|-------|----------------|
| Architect / Planner | Claude Opus | Architecture decisions, planning, complex reasoning, code review |
| Implementer | Claude Sonnet | Code generation, implementation, testing, refactoring |
| Quick Tasks | Claude Haiku | Lookups, formatting, simple edits, schema generation |

**Workflow:** Each phase is implemented as a series of Claude Code sessions. Subagents handle parallel implementation tasks. Custom skills will be created for repeatable patterns (schema generation, agent scaffolding, test generation).

---

## Phased Implementation Plan

### Phase 0 — Core Loop Proof (Weeks 1-4) ✅ COMPLETE

**Status:** Delivered. 211 passing tests. See `docs/phase0-test-results.md`.

**Goal:** Prove the fundamental concept — graph-based task management with a single AI agent reasoning loop.

**Scope:**
- Single Postgres instance with Apache AGE extension (graph) + pgvector (embeddings)
- Single user, no multi-tenancy
- CLI interface only (no channels)
- Core node types: Atomic, Composite, Delegated, Follow-up, Goal, Constraint
- Basic 7-factor scoring (hardcoded weights, no learning)
- Simple state machine (PENDING -> ACTIVE -> COMPLETED -> ARCHIVED)
- Local file system instead of S3 (swap later via StorageClient interface)
- No Redis (direct DB reads acceptable at single-user scale)

**Key deliverables:**
1. Graph schema (AGE) with core node/edge types
2. Orchestrating agent: single-trigger CLI invocation -> load graph -> score -> recommend actions
3. Task CRUD via CLI
4. Basic dependency resolution (DEPENDS_ON edges)
5. Docker Compose for local dev (Postgres + AGE + app)

**Tech stack:** Python 3.12+, Anthropic SDK (Claude), Postgres + AGE, Docker

---

### Phase 1 — Single-User System (Weeks 5-12) ✅ COMPLETE

**Status:** Delivered. 485+ passing unit tests. Plugin architecture refactored (4-layer ABC+Factory pattern).

**Delivered workstreams:**
- WS-F Gateway: FastAPI app, email channel plugin (IMAP/SMTP), Swagger UI, ChannelAdapter ABC, channel_registry
- WS-G Triggers: TriggerEngine (scheduled/inbound/on-demand), follow-up timing model
- WS-H Skills: SKILL.md parser, SkillWorker pool, HeartbeatMonitor, LLMRouter (LiteLLM)
- WS-I Infra: StorageClient (S3/MinIO), MessageBroker (Redis), SecretsClient (env_file), AsyncLogger
- WS-J Inbound: Task resolver (ID regex + pgvector), status signal extractor, processor pipeline
- WS-K Briefing: format_briefing(), AgentLoop scoring cycle orchestration
- Plugin refactoring: GraphStore/GraphQueryEngine ABCs, create_graph_store() factory, LLMClient ABC, create_llm_client() factory

**Goal:** Working single-user system with one communication channel and skill agents.

**Scope:**
- All 11 task node types + 3 coordination + 3 context nodes
- Full state machine with all transitions
- Email channel (IMAP polling + SMTP send — simplest to implement, no webhook infra needed)
- Inbound Update Protocol (Section 8) — text matching + embedding matching
- Follow-up timing model
- Daily briefing generation
- First 3 skill agents: Research, Email Drafter, Report Writer
- S3-compatible storage (MinIO locally, S3 in prod) via StorageClient
- Message broker (BullMQ on Redis for local, SQS in prod) via MessageBroker interface
- Heartbeat protocol for skill agents

**Key deliverables:**
1. Channel gateway container (email only)
2. Trigger engine (time-based + event-based + inbound + on-demand)
3. Skill Agent Runtime with async thread workers
4. SKILL.md format parser and executor
5. S3 file system layout (agents/, workspaces/, skills/)
6. status.md event-driven completion pipeline
7. AsyncLogger with structured JSON logging (Sec 32.4) — session_id tracing from day one
8. SecretsClient abstraction (Sec 31.6) — EnvFileClient for local dev

**Dependencies:** Phase 0 complete, MinIO running, Redis running

---

### Phase 2 — Multi-Channel + Organizations (Weeks 13-20) ✅ COMPLETE

**Status:** Delivered. Code present in `src/graphclaw/gateway/channels/`. Channel adapters for WhatsApp, Telegram, Slack, and Teams implemented alongside Phase 1 gateway work.

**Goal:** Add WhatsApp and Telegram. Introduce organization workspaces.

**Scope:**
- WhatsApp Business API integration (webhook + HMAC auth)
- Telegram Bot API integration (webhook + secret token auth)
- Channel-agnostic conversation context (Redis cache)
- Channel switching within active conversations
- Organization workspaces with isolation boundaries
- Alias system for cross-channel identity
- Secrets management integration (AWS Secrets Manager / Vault)
- Attachment handling (extract at gateway, store to S3)

**Delivered workstreams:**
- WS-P2-A Channel adapters: `channels/whatsapp/`, `channels/telegram/`, `channels/slack/`, `channels/teams/` — each with adapter, config, normalizer, sender
- WS-P2-B Conversation context cache: `gateway/context_cache.py`
- WS-P2-C Attachment handler: `gateway/attachment_handler.py`
- WS-P2-D Alias resolver: `gateway/alias_resolver.py`
- WS-P2-E Rate limiter: `gateway/rate_limiter.py`
- WS-P2-F SES inbound receiver: `channels/email/ses_receiver.py`

**Key deliverables:**
1. Multi-channel gateway with per-channel authentication ✅
2. Normalized InboundMessage format ✅
3. Active conversation cache (Redis) ✅
4. Organization node + workspace isolation ✅
5. Alias resolution system ✅

**Dependencies:** Phase 1 channel gateway, Redis operational

---

### Phase 3 — Multi-User + Delegation + Security (Weeks 21-28) ✅ COMPLETE

**Status:** Delivered. Auth stack, JWT lifecycle, BYOK, provisioning, delegation, escalation, and GDPR compliance all implemented.

**Goal:** Multiple users, cross-user delegation, full auth stack.

**Delivered workstreams:**
- WS-P3-A Auth: `auth/oauth.py` (OAuth 2.0 + PKCE, Google/Microsoft/GitHub), `auth/jwt.py` (RS256, 15-min expiry, refresh rotation, Redis jti revocation), `auth/middleware.py`, `auth/routes.py`
- WS-P3-B Provisioning: `auth/provisioning.py` — atomic UserNode + S3 prefix + IAM role + SQS queue creation with rollback
- WS-P3-C Delegation + Escalation: `agent/delegation.py`, `agent/escalation.py`
- WS-P3-D BYOK: `infra/byok.py` — per-user LLM key stored in SecretsClient, never in DB
- WS-P3-E Compliance: `compliance/` — GDPR erasure pipeline (`gdpr.py`), audit log (`audit.py`), data export (`export.py`), compliance models
- WS-P3-F Visibility grants: `models/nodes.py` — VisibilityGrant model, node-level access control
- WS-P3-G Scoring weights: `models/scoring_weights.py` — per-user W1–W7 weights with EMA learning

**Key deliverables:**
1. Node-level visibility grant system ✅
2. User onboarding provisioning flow (atomic, with rollback) ✅
3. OAuth 2.0 auth server + JWT issuance/refresh/revocation ✅
4. BYOK LLM key flow ✅
5. GDPR erasure + data export ✅
6. Delegation and escalation agent logic ✅

**Deferred to infra phase:**
- Container-per-user orchestration (Kubernetes/Fargate) — requires cloud infra
- Per-user IAM role provisioning (AWS SDK calls) — requires cloud infra

**Dependencies:** Phase 2 complete, container orchestration infra, IdP OAuth client registrations

---

### Phase 4 — Agent Interop, MCP Integration, Connectors & Skill Registry (Weeks 29-36) 🔄 IN PROGRESS

> **Note:** Web UI is a separate project (`graphclaw-cockpit`) — see `docs/ui-requirements.md`

**Status:** Core modules implemented (logic layer complete). `/app/v1/` API router exists but 21 endpoints are stubs. Next action: implement stub endpoints and cockpit backend API modules. See `docs/cockpit-backend-api-prd.md` for the full endpoint backlog.

**Goal:** A2A protocol, MCP server integration, calendar and import connectors, expanded skill registry, application API layer.

**Workstream status:**

| ID | Workstream | Status | Location |
|----|-----------|--------|----------|
| WS-P4-A | A2A REST API | ✅ Complete | `a2a/` — key manager, middleware, models, routes |
| WS-P4-B | Connectors ABC + Calendar + Import adapters | ✅ Complete | `connectors/` — Google Calendar, Outlook, Jira, Asana, Notion |
| WS-P4-C | Skill Registry v2 | ✅ Complete | `skills/registry.py`, `skills/registry_models.py` |
| WS-P4-D | MCP Server Registry + Client Runtime | ✅ Complete | `mcp/registry.py`, `mcp/client.py`, `mcp/official_registry.py` |
| WS-P4-E | Pre-built MCP Adapters + GATED approval workflow | ✅ Complete | `mcp/adapters/` (GitHub, Google Calendar, Slack), `mcp/approval.py` |
| WS-P4-F | Application API layer (/app/v1/ router) | ⚠️ Partial | `api/router.py` exists; 21 stubs need real implementations |
| WS-P4-G | Cockpit backend API modules (new) | ✅ Complete — all 6 waves delivered | `api/deps.py`, `api/graph.py`, `api/scoring.py`, `api/state.py`, `api/events.py`, `api/chat.py`, `api/config.py`, `api/secrets.py`, `api/agent.py`, `api/agents.py`, `api/settings.py` (+11), `api/skill_registry.py` (+4), `api/mcp_registry.py` (+2), `api/admin/` (9 modules) |
| WS-P4-H | Phase 4 test suite | ✅ Partial | Tests exist for a2a, mcp, connectors, api, auth |

**6-wave build plan (WS-P4-F + WS-P4-G completion):**

| Wave | Files | Endpoints | Cockpit Surface Unlocked |
|------|-------|-----------|--------------------------|
| **Wave 1** ✅ | `api/deps.py`, `api/graph.py`, `api/scoring.py`, `api/state.py`, `api/events.py` | 18 + SSE | Graph Cockpit, Task Views, Explainability, real-time updates |
| **Wave 2** ✅ | `api/approvals.py`, `api/settings.py`, `api/skill_registry.py`, `api/mcp_registry.py` | 15 | All stubs replaced with real implementations |
| **Wave 3** ✅ | `api/chat.py`, `api/config.py`, `api/secrets.py` | 10 | Chat interface, agent config editor, secrets vault |
| **Wave 4** ✅ | `api/settings.py` (+11 new), `api/agent.py` (6 new) | 17 | Full settings panel, agent monitor dashboard |
| **Wave 5** ✅ | `api/skill_registry.py` (+4), `api/mcp_registry.py` (+2), `api/agents.py` (7) | 13 | Skill marketplace, MCP scope view, canvas editor backend |
| **Wave 6** ✅ | `api/admin/` (9 files: members, features, llm, judge, guardrails, sso, audit, infra, connectors) | 45 | Full admin panel |

**Total remaining:** 118 endpoints across 22 files (18 new + 4 extended + `router.py`). See `docs/cockpit-backend-api-prd.md` for full endpoint backlog.

**Dependencies:** Phase 3 APIs stable

---

### Phase 4.5 — Intelligence Layer (Weeks 37-40) ✅ COMPLETE

**Status:** Delivered. 1567 unit tests passing (57 new tests added in Phase 4.5). See `WS-P45-F-SUMMARY.md` and `verify_changes.py` / `verify_ws_p45_f.py` for verification.

**Goal:** Close the context gap — every task node accumulates a communication log across all channels; every inbound message from any channel is classified and routed to the correct node or Betty's memory; Betty's agent actions and all communication events are durably logged to MinIO/S3 in a PII-safe structured format.

**Design doc:** `docs/architecture/intelligence-layer.md`  
**PRD sections:** 36 (Node Intelligence Layer), 37 (Embedding Pipeline)  
**Test scenarios:** 5–9 in `docs/test-scenarios.md`

**Dependencies:** Phase 4 complete, Docker stack running (gateway + MinIO + Redis + DB), `OPENAI_API_KEY` for embeddings.

**Workstreams and agent assignments:**

| ID | Workstream | Agent | Files | Phase order |
|----|-----------|-------|-------|-------------|
| WS-P45-A | Embedding Pipeline | `ws-a-database` + `ws-i-storage-logging` | `infra/embeddings.py` (new), `db/age/repository.py`, `inbound/resolver.py` | **First — prerequisite** |
| WS-P45-B | Structured Log Sink | `ws-i-storage-logging` | `infra/logger.py`, `gateway/deps.py` | Parallel with A |
| WS-P45-C | Node Intelligence Field | `ws-b-models` + `ws-a-database` | `models/nodes.py`, `db/age/repository.py` | Parallel with A+B |
| WS-P45-D | InboundIntelligenceAgent | `ws-j-inbound-protocol` | `inbound/intelligence_agent.py` (new) | After A+C |
| WS-P45-E | Event Consumer wiring + direct INBOUND consumer | `ws-e-cli-agent` | `agent/event_consumer.py`, `gateway/app.py` | After D |
| WS-P45-F | AgentLoop: logger + graph summary + check_inbox tool | `ws-e-cli-agent` | `agent/loop.py` | After B+C |
| WS-P45-G | Outbound intelligence logging + CheckinNode wiring | `ws-e-cli-agent` | `agent/event_consumer.py`, `db/age/repository.py` | After D+E |
| WS-P45-H | Inbox summarize-and-archive + storage paths | `ws-i-storage-logging` | `infra/storage.py`, `agent/event_consumer.py` | After E |

**Build order:**
```
Wave 1 (parallel): WS-P45-A (embeddings), WS-P45-B (log sink), WS-P45-C (node intelligence model)
Wave 2 (parallel): WS-P45-D (InboundIntelligenceAgent), WS-P45-F (AgentLoop tool)
Wave 3 (parallel): WS-P45-E (event consumer wiring), WS-P45-G (outbound logging), WS-P45-H (inbox)
```

**Key deliverables:**
1. `EmbeddingClient` + embedding generation on task create/update + `TaskResolver` vector search wired ✓
2. `AsyncLogger` MinIO/S3 sink with `min_level` filter and PII-safe allowlist event models ✓
3. `intelligence: str | None` field on `TaskNode` + `GoalNode`; graph update helpers ✓
4. `InboundIntelligenceAgent` with three-tier resolution waterfall (thread → task ID → vector) ✓
5. Direct `INBOUND_MESSAGES` consumer in `AgentEventConsumer` (bypasses missing TriggerEngine in local dev) ✓
6. Fixed `InboundProcessor` instantiation (broken since Phase 1) ✓
7. Outbound intelligence log + `CheckinNode` creation + Redis checkin key + reply linking ✓
8. Inbox two-track storage (`recent/` compact + `archive/` full) + `check_inbox` tool for Betty ✓
9. Unmatched inbound → Betty asks user for direction ✓
10. Structured JSONL logs in `{user_id}/logs/` + `_system/logs/` — CloudWatch-ingestible ✓

**Scope explicitly excluded:**
- GoalNode intelligence auto-update from inbound (goals not matched by InboundProcessor — deferred)
- `link_message_to_task` tool (Betty can ask user; tool to action response deferred)
- LLM re-consolidation of intelligence field when > 500 words (deferred — simple truncation in Phase 4.5)
- MinIO SSE-KMS encryption for archive/ entries (required for production, deferred to Phase 5)

---

### Phase 5 — Sub-Agent Parallel Orchestration (Weeks 41-46) 🔄 IN PROGRESS

**Goal:** The main `AgentLoop` can decompose work into parallel tasks, delegate each to a specialized sub-agent running in the background, receive structured typed updates, and re-engage once all delegated work completes.

**Design doc:** `C:\Users\abhis\.claude\plans\serene-booping-falcon.md`

**Design constraints:**
- Delegation is flat (max depth = 2): sub-agents cannot call `delegate_to_agent`
- Sub-agents use a dedicated `WorkerPool` (not shared with the orchestrator) to prevent starvation
- Crash recovery: mark task BLOCKED + escalate — no retry (prevents duplicate MCP writes/emails)
- Max concurrent sub-agents capped by `GRAPHCLAW_MAX_CONCURRENT_AGENTS` (default 4)
- Dispatch order driven by task graph `DEPENDS_ON` edges (topological sort)

**Workstream status:**

| ID | Workstream | Status | Files |
|----|-----------|--------|-------|
| WS-P5-A | Broker queues + config env vars | 🔄 Active | `infra/broker.py`, `infra/config.py` |
| WS-P5-B | SubAgentRunner + SubAgentPool | 🔄 Active | `agent/sub_agent_runner.py` (new), `agent/sub_agent_pool.py` (new) |
| WS-P5-C | Audit log events + AGENT_UPDATES consumer | 🔄 Active | `infra/logger.py`, `agent/event_consumer.py`, `agent/result_collector.py` |
| WS-P5-D | AgentDispatchPlanner + BatchCoordinator | 🔄 Active | `agent/dispatch_planner.py` (new) |
| WS-P5-E | AgentHealthMonitor + heartbeat escalation | 🔄 Active | `agent/health_monitor.py` (new), `skills/heartbeat.py` |
| WS-P5-F | AgentLoop: delegate_to_agent publishes + planner integration | 🔄 Active | `agent/loop.py` lines 1849–1920, lines 474–524 |
| WS-P5-G | Gateway wiring (lifespan startup/shutdown) | 🔄 Active | `gateway/app.py` |
| WS-P5-H | Test suite (unit + integration) | 🔄 Active | `tests/test_agent/test_sub_agent_*.py`, `tests/test_agent/test_dispatch_planner.py` |

**Build order:**
```
Wave 1 (parallel): WS-P5-A (queues+config), WS-P5-C (audit events)
Wave 2 (parallel): WS-P5-B (SubAgentRunner+Pool), WS-P5-D (DispatchPlanner+BatchCoordinator)
Wave 3 (parallel): WS-P5-E (HealthMonitor), WS-P5-F (AgentLoop modifications)
Wave 4 (sequential): WS-P5-G (gateway wiring), WS-P5-H (tests)
```

**New broker queues:**

| Queue | Constant | Publisher | Consumer |
|-------|----------|-----------|----------|
| `agent_jobs` | `AGENT_JOBS` | `AgentLoop._tool_delegate_to_agent()` | `SubAgentPool` |
| `agent_updates` | `AGENT_UPDATES` | `SubAgentRunner` | `AgentEventConsumer._consume_agent_updates_loop()` |

**New config env vars:**
- `GRAPHCLAW_MAX_CONCURRENT_AGENTS` (default 4)
- `GRAPHCLAW_SUBAGENT_WORKER_POOL_SIZE` (default 4)
- `GRAPHCLAW_AGENT_HEARTBEAT_INTERVAL_SECONDS` (default 60)
- `GRAPHCLAW_AGENT_HEARTBEAT_TIMEOUT_SECONDS` (default 300)

**Key deliverables:**
1. `SubAgentRunner` — async lifecycle (IDLE→RUNNING→COMPLETED/FAILED), LLM tool loop, heartbeat emit, typed event publish
2. `SubAgentPool` — bounded runner pool, `AGENT_JOBS` consumer, overflow queued in broker
3. `BatchCoordinator` — tier completion tracking, next-tier dispatch, `DELEGATION_COMPLETE` trigger
4. `AgentDispatchPlanner` — topological sort over task `DEPENDS_ON` subgraph → ordered parallel tiers
5. `AgentHealthMonitor` — heartbeat tracking per agent_id, BLOCKED + escalation on timeout
6. `AgentLoop._tool_delegate_to_agent()` — publishes `AgentJobEvent` to `AGENT_JOBS` after MinIO write
7. `AgentEventConsumer._consume_agent_updates_loop()` — third background task; routes typed events
8. 5 new audit log event classes in `AsyncLogger` with `agent_id + task_id` attribution
9. Gateway lifespan wires `SubAgentPool`, dedicated `WorkerPool` for sub-agents, `AgentHealthMonitor`
10. Full test suite: unit (planner, coordinator, runner state machine) + integration (delegation cycle)

---

### Phase 5 — Scale, Observability, Enterprise (Weeks 41-52)

**Goal:** Production hardening at 1,000+ users, full observability stack, enterprise features, compliance.

**Scope:**
- Slack and Microsoft Teams channel integration
- GDPR compliance (right-to-erasure with PII anonymization, data export)
- Rate limiting on all external APIs
- DDoS protection (WAF, CloudFront/Cloudflare)
- Encryption at rest for all storage layers
- SOC 2 audit trail
- Performance optimization for 500+ task graphs
- Progressive loading enforcement per trigger type
- Data residency controls (EU, US, APAC)
- **CloudWatch log group architecture** (Sec 32.2) — per-user groups for agent-runtime, shared groups for platform containers
- **CloudWatch metric filters** (Sec 32.6) — LLM token cost monitoring, cost anomaly detection (3-sigma), daily budget caps
- **Three-tier alerting model** (Sec 32.7) — P1 pages (state-at-risk), P2 alerts (degraded), P3 dashboards (trends)
- **CloudWatch dashboards** (Sec 32.11) — platform health, LLM cost, latency, reliability, user activity
- **Database backup & PITR** (Sec 32.8) — RDS automated snapshots, 35-day retention, S3 versioning, recovery procedures
- **Rolling deployment** (Sec 32.9) — zero-downtime via ECS/EKS rolling updates, heartbeat.md recovery absorbs restarts
- **MD file schema migration** (Sec 32.10) — forward-only, non-destructive, version-stamped, idempotent migration jobs
- **Log scrubbing** (Sec 32.3) — reject `sk-ant-*`, `wg_agent_*`, `Bearer` patterns before durable storage
- **X-Ray integration** (Sec 32.5, optional) — visual trace maps layered on session_id
- **Container scaling hardening** (Sec 28.11) — idle-to-zero for `agent-runtime` (Fargate Spot / KEDA on Redis Stream depth); `channel-gateway` 2–4 replicas + ALB; `trigger-engine` per-user briefing jitter + horizontal queue-processor replicas
- **Graph DB production hardening** (Sec 28.11) — PgBouncer connection pool, read replica for scoring/briefing queries, AGE indexes on vlabel/user_id/state/due_date, 5-second query timeout enforcement
- **SES inbound routing** (Sec 28.11) — replace IMAP polling with SES → S3 → Lambda → gateway POST for production email ingest; eliminates long-lived IMAP connection pool
- **Redis Cluster HA** (Sec 28.11) — 3-node Redis Cluster with consistent hashing by USER-id prefix; `relational-db` monthly partition on audit_log table

**Key deliverables:**
1. Enterprise channel integrations (Slack, Teams)
2. Compliance framework (GDPR, SOC 2)
3. Container auto-scaling: idle-to-zero (`agent-runtime`), horizontal replicas (`channel-gateway`, `trigger-engine` queue processors, `api-server`)
4. CloudWatch observability stack (log groups, metric filters, dashboards, alarms)
5. Backup/recovery procedures with tested runbooks
6. Rolling deployment pipeline with schema migration
7. Graph DB production hardening: PgBouncer + read replica + AGE index tuning
8. SES inbound email routing (replaces IMAP in production)
9. Redis Cluster HA + `relational-db` audit_log partitioning
10. Performance benchmarks: 1,000-user load test with pass/fail thresholds

---

## Agent Integration Recommendations

### Orchestrating Agent

- **Recommended:** Direct Anthropic SDK (Claude) — the agent invocation pattern is highly custom (stateless call with file-loaded context). Heavy frameworks add abstraction without value here.
- **Multi-provider:** Use LiteLLM as a thin proxy for skill agents that may use different LLM providers. The orchestrating agent itself should stay on Claude for reasoning quality.
- **Why NOT LangChain/CrewAI/AutoGen:** These frameworks assume long-running agent loops, tool-calling patterns, and memory abstractions that conflict with the "stateless invocation, stateful files" principle. The file-based brain IS the memory layer — adding another on top creates impedance mismatch.

### Skill Agents to Build/Integrate

| Agent | Purpose | Phase | Build vs. Integrate |
|-------|---------|-------|---------------------|
| Research Agent | Web search + summarization for Research tasks | 1 | Build (Tavily API + Claude) |
| Email Drafter | Compose emails from task context | 1 | Build (Claude + templates) |
| Report Writer | Weekly/monthly report generation | 1 | Build (Claude + graph data) |
| Meeting Notes Agent | Transcribe + structure meeting notes | 2 | Integrate (Whisper API + Claude) |
| LinkedIn Outreach Agent | Draft personalized outreach messages | 3 | Build (Claude + profile data) |
| Pipeline Report Agent | Aggregate prospect status for BD reporting | 3 | Build (graph query + Claude) |
| MCP Tool Agent | Execute tool calls via registered MCP servers | 4 | Build (MCP SDK + Claude tool-use) |
| Calendar Sync Agent | Bi-directional calendar awareness | 4 | Build (Google/Outlook APIs) |
| Import Agent | Parse external tool exports into graph nodes | 4 | Build (per-source adapters) |
| Monitoring Agent | Infrastructure health, alert on anomalies | 5 | Integrate (Prometheus + AlertManager) |

### Agent-to-Agent Protocol Partners

- **Google A2A Protocol** — Open standard for agent interop. Support as external agent communication layer. Delivered in Phase 4.
- **MCP (Model Context Protocol)** — Tool/context sharing between agents. Orchestrating agent acts as MCP client; pre-built server adapters connect calendar, GitHub, Slack, Jira, and Notion; skill agents can expose and consume MCP tools. Delivered in Phase 4 (Section 34).

---

## Public Repos & Libraries to Accelerate Development

### Phase 0 — Core Loop

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `apache/age` | Graph extension for Postgres | Eliminates need for separate Neo4j; Cypher queries on Postgres |
| `pgvector/pgvector` | Vector similarity search in Postgres | Embedding matching for inbound updates, single DB engine |
| `anthropics/anthropic-sdk-python` | Claude API client | Direct LLM calls for orchestrating agent |
| `docker/compose` | Local dev orchestration | `docker compose up` for the full stack |

### Phase 1 — Single-User System

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `minio/minio` | S3-compatible local storage | Local dev parity with S3 |
| `taskiq-python/taskiq` / `OptimalBits/bull` | Async task queue | MessageBroker abstraction for local dev and prod |
| `pydantic/pydantic` | Data validation | Node/edge schema validation, SKILL.md frontmatter parsing |
| `tiangolo/fastapi` | API framework | Channel gateway, A2A API, settings panel API |
| `tavily-ai/tavily-python` | Web search API | Research Agent skill — structured web search |
| `jmorganca/ollama` | Local LLM inference | Cost-free skill agent testing during development |

### Phase 2 — Multi-Channel

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `whatsapp-api-client/whatsapp-api-client-python` | WhatsApp Business API client | Accelerates WhatsApp channel integration |
| `python-telegram-bot/python-telegram-bot` | Telegram Bot API | Mature, async-native Telegram integration |
| `redis/redis-py` | Redis client | Conversation cache, write-through caching |

### Phase 3 — Multi-User

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `kubernetes-client/python` | K8s API client | Container-per-user lifecycle management |
| `hashicorp/vault` | Secrets management | Cloud-agnostic secrets (LLM keys, channel creds) |
| `google/A2A` | Agent-to-Agent protocol | Standard interop protocol for external agents |

### Phase 4 — Visual Interface

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `cytoscape/cytoscape.js` | Graph visualization | Interactive task graph rendering in the browser |
| `xyflow/xyflow` (React Flow) | Node-based UI | Alternative to Cytoscape for React-native graph editing |
| `shadcn/ui` | UI component library | Rapid web UI development |
| `TanStack/query` | Data fetching | Efficient API data management for the web UI |

### Phase 3 — Security & Auth

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `authlib/authlib` | OAuth 2.0 / OIDC client | OAuth flow with PKCE, JWT validation, IdP integration |
| `mpdavis/python-jose` | JWT creation/validation | RS256 platform JWT issuance and verification |
| `boto3` (AWS SDK) | IAM role provisioning, Secrets Manager | Per-user IAM roles, secret CRUD, S3 prefix policies |

### Phase 4 — Agent Interop & MCP

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `google/A2A` | Agent-to-Agent protocol | Standard interop protocol for external agents |
| `modelcontextprotocol/python-sdk` | MCP client + server SDK | Orchestrating agent as MCP client; skill agents expose MCP tools |
| `mcp-server-calendar` (Google) | Google Calendar MCP server | Calendar awareness for scheduling and deadline reasoning |
| `mcp-server-github` | GitHub MCP server | PR status, issue tracking, commit context in task graph |
| `mcp-server-slack` | Slack MCP server | Read Slack threads, post updates from task context |

### Phase 5 — Enterprise & Observability

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `slackapi/bolt-python` | Slack integration | Enterprise channel support |
| `microsoftgraph/msgraph-sdk-python` | Microsoft Teams/Outlook | Enterprise channel + calendar integration |
| `boto3` CloudWatch Logs | Log aggregation | Structured JSON logs with per-user log groups (Sec 32.2) |
| `aws/aws-xray-sdk-python` | Distributed tracing (optional) | Visual trace maps layered on session_id (Sec 32.5) |

### Skill Development Accelerators

| Repo/Library | Purpose | How it helps |
|---|---|---|
| `anthropics/anthropic-cookbook` | Claude usage patterns | Prompt engineering patterns for skill agents |
| `BerriAI/litellm` | Multi-LLM proxy | Skills can use any LLM provider through unified API |
| `modelcontextprotocol/python-sdk` | MCP SDK | Skills can expose/consume tools via MCP |
| `anthropics/courses` | Claude best practices | Agent design patterns, tool use, structured output |
