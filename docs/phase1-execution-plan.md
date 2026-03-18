# Phase 1 Execution Plan — Agent Infrastructure & Communication

**Timeline:** Weeks 5–12 (8 weeks)
**Status:** Planning Complete — Ready for Execution
**Predecessor:** Phase 0 (Core Loop Proof) — Complete at commit `a87676e`

---

## Objective

Build the multi-agent infrastructure that transforms GraphClaw from a local CLI tool into a
service-based system capable of receiving inbound updates (email), running autonomous skill
agents, generating daily briefings, and delivering outbound notifications — all through
abstract interfaces that work locally (Redis/MinIO/env files) and in production (SQS/S3/Vault).

---

## Deliverables (from build-plan.md)

| # | Deliverable | PRD Sections | Workstream |
|---|-------------|--------------|------------|
| 1 | Channel Gateway (FastAPI + email IMAP/SMTP) | 14, 15 | WS-F |
| 2 | Inbound Update Protocol (6-step pipeline) | 8 | WS-J |
| 3 | Trigger Engine (time/event/inbound/on-demand) | 10 | WS-G |
| 4 | Skill Agent Runtime (SKILL.md, workers, heartbeat) | 30 | WS-H |
| 5 | Daily Briefing Generation (5-section format) | 12 | WS-K |
| 6 | Follow-up Timing Model (adaptive cadence) | 10 | WS-G |
| 7 | Storage Abstractions (S3/MinIO, Secrets, Logger) | 26, 31.6, 32.4 | WS-I |
| 8 | Message Broker Abstraction (Redis/SQS) | — | WS-I |

---

## Architecture: Message Flow

```
┌──────────────┐    inbound_messages    ┌────────────────┐    trigger_events    ┌───────────────┐
│   Channel    │ ──────────────────────▶│    Trigger     │ ──────────────────▶ │    Agent      │
│   Gateway    │                        │    Engine      │                     │   Runtime     │
│  (FastAPI)   │◀────────────────────── │                │                     │  (Scoring)    │
└──────────────┘    outbound_messages   └────────────────┘                     └───────┬───────┘
       │                                       │                                       │
       │                                       │                               skill_jobs
  IMAP/SMTP                              Scheduler                                     │
  (Email)                               (Cron-like)                                    ▼
                                                                               ┌───────────────┐
                                                                               │  Skill Worker  │
                                                                               │     Pool       │
                                                                               └───────┬───────┘
                                                                                       │
                                                                               status_updates
                                                                                       │
                                                                                       ▼
                                                                               ┌───────────────┐
                                                                               │   Briefing    │
                                                                               │  Generator    │
                                                                               └───────────────┘
```

**Broker Queues:**
- `inbound_messages` — Gateway → Trigger Engine
- `trigger_events` — Trigger Engine → Agent Runtime
- `skill_jobs` — Agent Runtime → Skill Worker Pool
- `status_updates` — Skill Workers → Agent Runtime
- `outbound_messages` — Agent Runtime → Gateway

---

## Skills Inventory (Phase 1)

### New Skills (6)
| Skill | Purpose |
|-------|---------|
| `fastapi-gateway-patterns` | FastAPI app structure, IMAP/SMTP, message normalization |
| `trigger-engine-patterns` | Trigger types, scheduler, follow-up timing, briefing structure |
| `skill-agent-runtime` | SKILL.md parsing, worker pool, heartbeat, LiteLLM routing |
| `storage-abstractions` | StorageClient, SecretsClient, AsyncLogger, S3 layout |
| `inbound-protocol-patterns` | 6-step resolution pipeline, status signals, cascade |
| `message-broker-patterns` | MessageBroker ABC, Redis impl, queue names |

### Inherited Skills (from Phase 0)
| Skill | Used By |
|-------|---------|
| `age-cypher-patterns` | WS-J (embedding search queries) |
| `graphclaw-scoring-algorithm` | WS-G, WS-J, WS-K (scoring integration) |
| `graphclaw-state-machine` | WS-J (cascade state transitions) |
| `graphclaw-test-patterns` | All workstreams |
| `graphclaw-docker-dev` | WS-I (Docker Compose updates) |
| `code-architecture-review` | Phase 1 Reviewer |
| `code-security-review` | Phase 1 Reviewer |

---

## Agents

### Implementation Agents (6 Sonnet)

| Agent | Workstream | Skills | Depends On |
|-------|------------|--------|------------|
| `ws-f-channel-gateway` | WS-F | fastapi-gateway, message-broker, test | WS-A, WS-B |
| `ws-g-trigger-engine` | WS-G | trigger-engine, message-broker, scoring, test | WS-A, WS-B, WS-D |
| `ws-h-skill-runtime` | WS-H | skill-agent-runtime, storage, message-broker, test | WS-F, WS-I |
| `ws-i-storage-logging` | WS-I | storage, message-broker, docker-dev, test | (none) |
| `ws-j-inbound-protocol` | WS-J | inbound-protocol, age-cypher, scoring, state-machine, test | WS-F, WS-G, WS-I, WS-A |
| `ws-k-briefing-status` | WS-K | trigger-engine, skill-runtime, inbound-protocol, storage, scoring, test | WS-G, WS-H, WS-J |

### Review Agent (1 Opus)

| Agent | Role | Skills |
|-------|------|--------|
| `phase1-reviewer` | Architect review at 5 checkpoints | arch-review, security, best-practices, simplification + Phase 1 domain skills |

### Inherited Agents (Phase 0)

| Agent | Reuse |
|-------|-------|
| `architect-reviewer` | Available for cross-phase review |
| `documentation-reviewer` | Post-implementation header + comment pass |

**Total Active Agents: 9** (6 implementation + 1 new reviewer + 2 inherited)

---

## Execution Timeline

### Wave 1: Foundation (Weeks 5-6) — 3 agents parallel

```
WS-I (Storage & Logging)     ████████████████████  ← No dependencies, starts immediately
WS-F (Channel Gateway)       ████████████████████  ← Phase 0 models/DB already exist
WS-G (Trigger Engine)        ████████████████████  ← Phase 0 scoring engine exists
```

**WS-I: Storage & Logging Infrastructure** (Sonnet, ~1.5 weeks)
- Day 1-2: StorageClient ABC + S3StorageClient with MinIO
- Day 3-4: SecretsClient ABC + EnvFileSecretsClient
- Day 5-6: MessageBroker ABC + RedisMessageBroker
- Day 7-8: AsyncLogger with structured JSON + flush loop
- Day 9-10: Docker Compose (Redis + MinIO), config updates, tests

**WS-F: Channel Gateway** (Sonnet, ~2 weeks)
- Day 1-3: FastAPI app factory, health endpoints, CORS, error handling
- Day 4-6: InboundMessage/OutboundMessage schemas, normalizer
- Day 7-9: IMAP polling loop (aioimaplib), reconnect with backoff
- Day 10-12: SMTP sender (aiosmtplib), outbound queue consumer
- Day 13-14: Integration tests, broker publish/consume wiring

**WS-G: Trigger Engine** (Sonnet, ~2 weeks)
- Day 1-3: TriggerEvent/TriggerConfig models, trigger persistence
- Day 4-6: Scheduled trigger loop (cron-like check every 60s)
- Day 7-9: Event consumer loop (broker → TriggerEvent dispatch)
- Day 10-11: Follow-up timing model (4-factor computation)
- Day 12-14: On-demand trigger endpoint, deduplication, tests

**🔍 Opus Review Checkpoint 1** (after Wave 1):
- Infrastructure layer correctness
- Gateway + trigger engine integration
- Message flow validation

### Wave 2: Runtime (Weeks 7-9) — 1 agent (depends on Wave 1)

```
WS-H (Skill Agent Runtime)   ████████████████████████████████
```

**WS-H: Skill Agent Runtime** (Sonnet, ~2.5 weeks)
- Day 1-3: SKILL.md parser (YAML frontmatter + markdown body)
- Day 4-6: SkillWorkerPool (asyncio.Semaphore, max_concurrent=5)
- Day 7-9: SkillWorker lifecycle (state machine: QUEUED→COMPLETE/FAILED)
- Day 10-12: Heartbeat protocol (5min interval, 15min timeout, 3 retries)
- Day 13-15: LiteLLM provider routing, context variable injection
- Day 16-18: status.md pipeline (write progress, completion signal via broker)

**🔍 Opus Review Checkpoint 2** (after WS-H):
- Worker isolation and concurrency safety
- Heartbeat reliability
- Failure recovery decision tree correctness

### Wave 3: Intelligence (Weeks 9-11) — 2 agents parallel

```
WS-J (Inbound Protocol)      ████████████████████████████
WS-K (Briefing & Status)     ████████████████████████████
```

**WS-J: Inbound Update Protocol** (Sonnet, ~2 weeks)
- Day 1-3: Task ID regex extraction + direct graph lookup
- Day 4-6: Vector embedding search (pgvector cosine similarity)
- Day 7-9: Status signal extraction + confidence assessment (LLM)
- Day 10-11: Follow-up child decision tree (5 branches)
- Day 12-14: Graph cascade propagation (AND/OR gates, cache invalidation)

**WS-K: Briefing & Status Pipeline** (Sonnet, ~2 weeks)
- Day 1-3: Status.md consumer (skill completion → graph update)
- Day 4-6: Briefing section builders (CRITICAL, INFERENCES, COMPLETED, UPCOMING, DEFERRED)
- Day 7-9: Scoring cycle integration (trigger → score → rank → partition)
- Day 10-11: LLM-based briefing formatter (concise/detailed styles)
- Day 12-14: Interrupt threshold logic, snoozed item tracking, tests

**🔍 Opus Review Checkpoint 3** (after Wave 3):
- Full pipeline: inbound → resolve → signal → cascade → score → brief
- Embedding search accuracy and thresholds
- Briefing quality and cognitive load limits

### Wave 4: Integration & Polish (Weeks 11-12)

```
Integration Testing           ████████████████████
Documentation Review          ████████████
Docker E2E                    ████████████
```

- End-to-end integration tests (email in → briefing out)
- Documentation reviewer agent pass on all new files
- Docker Compose full-stack verification (app + DB + Redis + MinIO)
- Performance baseline (broker throughput, skill execution latency)

**🔍 Opus Review Checkpoint 4** (Final):
- Cross-cutting: logging, tracing, secrets, auth readiness
- Design principle compliance (PRD Section 33)
- Phase 2 readiness assessment

---

## New Source Files (Phase 1)

```
src/graphclaw/
├── infra/                    # WS-I
│   ├── __init__.py
│   ├── storage.py            # StorageClient ABC + S3StorageClient
│   ├── secrets.py            # SecretsClient ABC + EnvFileSecretsClient
│   ├── broker.py             # MessageBroker ABC + RedisMessageBroker
│   ├── logger.py             # AsyncLogger (structured JSON)
│   └── config.py             # Infrastructure configuration
├── gateway/                  # WS-F
│   ├── __init__.py
│   ├── app.py                # FastAPI app factory
│   ├── schemas.py            # InboundMessage, OutboundMessage
│   ├── email_poller.py       # IMAP polling loop
│   ├── email_sender.py       # SMTP outbound
│   └── normalizer.py         # Email → InboundMessage
├── triggers/                 # WS-G
│   ├── __init__.py
│   ├── engine.py             # TriggerEngine main loop
│   ├── scheduler.py          # Cron-like trigger checker
│   ├── models.py             # TriggerEvent, TriggerConfig
│   └── followup.py           # Follow-up timing computation
├── skills/                   # WS-H
│   ├── __init__.py
│   ├── parser.py             # SKILL.md parsing
│   ├── worker_pool.py        # SkillWorkerPool
│   ├── worker.py             # SkillWorker lifecycle
│   ├── heartbeat.py          # Heartbeat monitoring
│   ├── llm_router.py         # LiteLLM provider mapping
│   └── status_pipeline.py    # status.md read/write
├── inbound/                  # WS-J
│   ├── __init__.py
│   ├── resolver.py           # Task ID + embedding resolution
│   ├── signals.py            # Status signal extraction
│   ├── followup.py           # Follow-up decision tree
│   ├── cascade.py            # Graph cascade propagation
│   ├── router.py             # Action decision routing
│   └── embeddings.py         # Embedding construction + search
└── briefing/                 # WS-K
    ├── __init__.py
    ├── generator.py           # Briefing generation pipeline
    ├── formatter.py           # LLM briefing formatting
    ├── sections.py            # Section builders
    └── status_consumer.py     # status.md → event pipeline

tests/
├── test_infra/               # WS-I tests
├── test_gateway/             # WS-F tests
├── test_triggers/            # WS-G tests
├── test_skills/              # WS-H tests
├── test_inbound/             # WS-J tests
├── test_briefing/            # WS-K tests
└── test_integration/
    └── test_phase1_e2e.py    # Full pipeline integration
```

**~35 new source files, ~25 new test files**

---

## New Dependencies (pyproject.toml additions)

```toml
[project.dependencies]
# Phase 1 additions
fastapi = ">=0.115"
uvicorn = {version = ">=0.34", extras = ["standard"]}
aioimaplib = ">=1.1"
aiosmtplib = ">=3.0"
redis = {version = ">=5.0", extras = ["hiredis"]}
boto3 = ">=1.35"
litellm = ">=1.50"
python-dotenv = ">=1.0"
```

---

## Docker Compose Updates

```yaml
# New services added to docker/docker-compose.yml
redis:
  image: redis:7-alpine
  ports: ["6379:6379"]
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]

minio:
  image: minio/minio:latest
  command: server /data --console-address ":9001"
  ports: ["9000:9000", "9001:9001"]
  environment:
    MINIO_ROOT_USER: graphclaw
    MINIO_ROOT_PASSWORD: graphclaw_dev
  healthcheck:
    test: ["CMD", "mc", "ready", "local"]
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| IMAP library maturity (aioimaplib) | Evaluate aiolib-imap as fallback; wrap in abstract interface |
| LiteLLM API compatibility | Pin version, integration test against actual Anthropic API |
| Redis message loss | Acknowledge pattern; consider Redis Streams for durability |
| Embedding model cost | Cache embeddings in pgvector; only recompute on node change |
| Worker pool resource exhaustion | Semaphore + max_concurrent limit; monitor via AsyncLogger |

---

## Success Criteria

1. ✅ `docker compose up` starts full stack (DB + Redis + MinIO + app)
2. ✅ Email received via IMAP → inbound message queue → trigger engine
3. ✅ Trigger engine dispatches events to agent runtime
4. ✅ Skill agents execute via worker pool with heartbeat monitoring
5. ✅ Daily briefing generated with 5 sections from scored action queue
6. ✅ All new tests pass (`pytest tests/test_infra/ tests/test_gateway/ tests/test_triggers/ tests/test_skills/ tests/test_inbound/ tests/test_briefing/`)
7. ✅ End-to-end test: simulated email → briefing output
8. ✅ Opus review passes all 4 checkpoints
