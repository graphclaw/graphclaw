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

### Phase 0 — Core Loop Proof (Weeks 1-4)

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

### Phase 1 — Single-User System (Weeks 5-12)

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

### Phase 2 — Multi-Channel + Organizations (Weeks 13-20)

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

**Key deliverables:**
1. Multi-channel gateway with per-channel authentication
2. Normalized InboundMessage format
3. Active conversation cache (Redis)
4. Organization node + workspace isolation
5. Alias resolution system

**Dependencies:** Phase 1 channel gateway, Redis operational

---

### Phase 3 — Multi-User + Delegation + Security (Weeks 21-28)

**Goal:** Multiple users, cross-user delegation, A2A protocol, full auth stack.

**Scope:**
- Multi-user graph with visibility grants (Section 25.2)
- Container-per-user runtime (with idle scaling)
- Cross-user delegation flow (Delegated tasks to external ResourceNodes)
- A2A REST API for external agents (Section 30.9)
- Onboarding & network growth (recruited users arrive with pre-seeded tasks)
- Conflict resolution: optimistic locking with version field on nodes
- Scoring weight learning: exponential moving average, updated on override signals
- Approval task escalation paths
- **OAuth 2.0 + PKCE auth flow** (Sec 31.3) — Google, Microsoft, GitHub IdPs
- **Platform JWT lifecycle** (Sec 31.3) — RS256, 15-min expiry, refresh token rotation, Redis jti revocation
- **IAM role-per-container architecture** (Sec 31.2) — user-scoped S3 prefix conditions
- **User onboarding provisioning** (Sec 31.8) — atomic creation of UserNode + S3 prefix + IAM role + SQS queue + container
- **Secrets Manager integration** (Sec 31.4) — full `/workgraph/` namespace, BYOK LLM key flow (Sec 31.5)
- **Attack surface mitigations** (Sec 31.7) — webhook HMAC, rate limits, A2A key scoping

**Key deliverables:**
1. Multi-tenant container orchestration (Kubernetes / Fargate)
2. Node-level visibility grant system
3. A2A API with key lifecycle
4. User onboarding provisioning flow (atomic, with rollback)
5. Optimistic locking on graph writes
6. OAuth 2.0 auth server + JWT issuance/refresh/revocation
7. Per-user IAM role provisioning
8. SecretsClient backends: AWSSecretsClient, HashiCorpVaultClient

**Dependencies:** Phase 2 complete, container orchestration infra, IdP OAuth client registrations

---

### Phase 4 — Visual Interface + Advanced Skills (Weeks 29-36)

**Goal:** Web-based graph visualization, expanded skill library, calendar integration.

**Scope:**
- Web UI: graph visualization (Cytoscape.js or react-flow), task management, settings panel
- Visual graph explorer with zoom/filter by goal, status, assignee
- Skill agent marketplace / registry
- Calendar integration (Google Calendar, Outlook) for scheduling awareness
- Bulk import from Jira, Asana, Notion (via their APIs)
- Advanced briefing styles (concise vs. detailed)
- Explainability dashboard (score breakdowns, decision audit trail)

**Key deliverables:**
1. React web application with graph visualization
2. REST/GraphQL API server
3. Calendar sync service
4. Import adapters for 3 external systems
5. Skill registry with versioning

**Dependencies:** Phase 3 APIs stable

---

### Phase 5 — Scale, Observability, Enterprise (Weeks 37-48)

**Goal:** Production hardening, full observability stack, enterprise features, compliance.

**Scope:**
- Slack and Microsoft Teams channel integration
- GDPR compliance (right-to-erasure with PII anonymization, data export)
- Rate limiting on all external APIs
- DDoS protection (WAF, CloudFront/Cloudflare)
- Encryption at rest for all storage layers
- SOC 2 audit trail
- Idle-to-zero container scaling
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

**Key deliverables:**
1. Enterprise channel integrations (Slack, Teams)
2. Compliance framework (GDPR, SOC 2)
3. Auto-scaling infrastructure
4. CloudWatch observability stack (log groups, metric filters, dashboards, alarms)
5. Backup/recovery procedures with tested runbooks
6. Rolling deployment pipeline with schema migration
7. Performance benchmarks at scale

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
| Calendar Sync Agent | Bi-directional calendar awareness | 4 | Build (Google/Outlook APIs) |
| Import Agent | Parse external tool exports into graph nodes | 4 | Build (per-source adapters) |
| Monitoring Agent | Infrastructure health, alert on anomalies | 5 | Integrate (Prometheus + AlertManager) |

### Agent-to-Agent Protocol Partners

- **Google A2A Protocol** — Open standard for agent interop. Support as external agent communication layer.
- **MCP (Model Context Protocol)** — Tool/context sharing between agents. Skills can expose/consume tools via MCP.

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
