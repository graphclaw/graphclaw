# GraphClaw — Graph-Based Task Orchestration System

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-485%2B%20passing-green)]()

**Domain:** [graphclaw.ai](https://graphclaw.ai) | **GitHub:** [abhishekgupta-myrepo/graphclaw](https://github.com/abhishekgupta-myrepo/graphclaw)

GraphClaw is an open-source graph-based task orchestration system where an AI agent manages tasks for humans and other agents via a property graph. Tasks, goals, constraints, and resources are modeled as graph nodes connected by typed edges (dependencies, assignments, blocking relationships). A 7-factor scoring algorithm continuously prioritizes the action queue, while a state machine enforces lifecycle invariants.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│          CLI (Typer + Rich) / Gateway API (FastAPI)          │
│   task list/show/create │ agent run/score/briefing │ /docs   │
├──────────────────────────────────────────────────────────────┤
│                  Channel Gateway Layer                       │
│  ChannelAdapter ABC │ Email (done) │ WhatsApp/Telegram (P2)  │
├────────────────────────┬─────────────────────────────────────┤
│   Agent Reasoning Loop │           Skill Runtime             │
│   fetch → score → act  │  SkillWorker │ SKILL.md │ LLMClient │
├────────────────────────┴─────────────────────────────────────┤
│                   LLM Provider Layer                         │
│  LLMClient ABC │ Anthropic │ OpenAI │ LiteLLM (pluggable)    │
├──────────────────────────────────────────────────────────────┤
│   State Machine │ Scoring Engine (7 factors) │ Trigger Engine│
├──────────────────────────────────────────────────────────────┤
│          Domain Models (Pydantic v2) — 17 node types         │
├──────────────────────────────────────────────────────────────┤
│   Database Layer — GraphStore ABC                            │
│   Postgres + Apache AGE (graph) + pgvector (embeddings)      │
└──────────────────────────────────────────────────────────────┘
```

## Plugin Architecture

GraphClaw is designed as a **pluggable 4-layer system**. Every infrastructure concern uses the ABC + Factory + Strategy pattern so backends are swappable without changing business logic:

| Layer | ABC | How to add a backend |
|-------|-----|----------------------|
| **Database** | `GraphStore` + `GraphQueryEngine` | Implement ABC, place in `src/graphclaw/db/<name>/` |
| **Gateway** | `ChannelAdapter` | Implement ABC, place in `src/graphclaw/gateway/channels/<name>/` |
| **LLM** | `LLMClient` | Implement ABC, place in `src/graphclaw/llm/<name>/` |
| **Infra** | `StorageClient`, `MessageBroker`, `SecretsClient` | Implement ABC, register in factory |

See [`docs/architecture.md`](../architecture.md) for the full design.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Graph DB | PostgreSQL + [Apache AGE](https://age.apache.org/) (Cypher queries) |
| Vectors | [pgvector](https://github.com/pgvector/pgvector) (embedding similarity) |
| API | FastAPI + Swagger UI (`/docs`) |
| AI Orchestration | Anthropic SDK / OpenAI SDK (pluggable via LLMClient ABC) |
| Multi-provider | LiteLLM (default), Anthropic, OpenAI (all swappable) |
| Validation | Pydantic v2 |
| CLI | Typer + Rich |
| Infrastructure | Docker Compose |
| Storage | MinIO (S3-compatible, local dev) |
| Caching/Broker | Redis (local dev) |

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.12+

### 1. Clone and start the stack

```bash
git clone https://github.com/abhishekgupta-myrepo/graphclaw
cd graphclaw
cp docker/.env.example docker/.env
docker compose -f docker/docker-compose.yml up -d
```

Note: this Docker workflow reads environment variables from `docker/.env`.
For non-Docker local runs (for example CLI or direct Python execution), use the repository root `.env`.

This starts Postgres+AGE+pgvector, MinIO, Redis, and the gateway service.

### 2. Install the project

```bash
pip install -e ".[dev]"
```

### 3. Run tests

```bash
# Unit tests (no DB required) — 485+ tests
pytest tests/ --ignore=tests/test_db -q

# Integration tests (requires running DB)
export TEST_DATABASE_URL=postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw_test
pytest tests/test_db/ -m integration
```

### 4. Use the CLI

```bash
# Score tasks and show action queue
graphclaw agent score

# Generate a briefing
graphclaw agent briefing

# Run one agent reasoning cycle
graphclaw agent run
```

### 5. Gateway API (Swagger UI)

Start the gateway service, then visit: `http://localhost:8080/docs`

```bash
# Or run directly
python -m graphclaw.gateway.app
```

## Project Structure

```
graphclaw/
├── src/graphclaw/
│   ├── agent/              # Orchestrating agent reasoning loop
│   ├── cli/                # CLI interface (Typer + Rich)
│   ├── db/                 # Database layer (GraphStore ABC + AGE backend)
│   │   ├── base.py         # GraphStore + GraphQueryEngine ABCs
│   │   ├── factory.py      # create_graph_store()
│   │   └── age/            # Postgres+AGE backend
│   ├── gateway/            # Channel gateway (ChannelAdapter ABC)
│   │   ├── channel_base.py # ChannelAdapter ABC
│   │   ├── channel_registry.py
│   │   └── channels/email/ # Email plugin (IMAP+SMTP)
│   ├── llm/                # LLM provider abstraction (LLMClient ABC)
│   │   ├── base.py         # LLMClient ABC + LLMMessage/LLMResponse types
│   │   ├── factory.py      # create_llm_client()
│   │   ├── anthropic/      # Anthropic SDK wrapper
│   │   ├── openai/         # OpenAI SDK wrapper
│   │   └── litellm/        # LiteLLM wrapper (default)
│   ├── infra/              # Storage, broker, secrets, logging ABCs
│   ├── inbound/            # Inbound Update Protocol
│   ├── models/             # Pydantic domain models (17 node types)
│   ├── scoring/            # 7-factor scoring engine
│   ├── skills/             # Skill agent runtime (SKILL.md parser, SkillWorker)
│   ├── state/              # State machine (10 states, guarded transitions)
│   └── triggers/           # Trigger engine (scheduled, inbound, on-demand)
├── tests/                  # pytest test suite (485+ tests)
├── docs/                   # Documentation
│   ├── architecture.md     # Plugin architecture overview
│   ├── llm-providers.md    # How to add an LLM provider
│   ├── channels.md         # How to add a gateway channel
│   ├── db-backends.md      # How to add a database backend
│   ├── api-reference.md    # Gateway API endpoints
│   └── skills-and-agents-roadmap.md
├── docker/                 # Docker Compose local dev stack
├── scripts/                # DB init + seed scripts
├── .claude/                # Claude Code configuration + skills
├── docs/planning/build-plan.md           # 6-phase implementation plan
├── docs/release-notes/CHANGELOG.md
├── docs/governance/documentation-governance.md
└── LICENSE                 # Apache 2.0
```

## Scoring Algorithm

The 7-factor weighted scoring formula prioritizes tasks:

| # | Factor | Weight | Description |
|---|--------|--------|-------------|
| W1 | Timeline Urgency | 0.25 | Days to deadline, effort slack |
| W2 | Dependency Weight | 0.20 | Direct + transitive downstream dependents |
| W3 | Critical Path | 0.20 | On critical path × goal priority multiplier |
| W4 | Blocker Score | 0.15 | Hard (1.0) / Soft (0.6) blocker elevation |
| W5 | Human Override | 0.10 | Prioritize (+1.0) / Deprioritize (−0.3) / Snooze (exclude) |
| W6 | Resource Risk | 0.05 | Reliability, load, risk signals |
| W7 | Constraint Pressure | 0.05 | Budget/time/resource constraint proximity |

**Post-multipliers:** Critical path P1 goal = 1.5×, P2 = 1.3×, P3 = 1.1×

## State Machine

10 task states with guarded transitions:

```
PENDING → ACTIVE → IN_PROGRESS → COMPLETE (terminal)
                  → BLOCKED → ACTIVE (when blocker resolves)
                  → DELAYED → IN_PROGRESS
                  → NEEDS_REVIEW → COMPLETE | IN_PROGRESS
        → CANCELLED (terminal)
        → SNOOZED → ACTIVE
        → INACTIVE_PENDING → ACTIVE (on predecessor completion)
```

## License

Copyright 2026 Abhishek Gupta. Licensed under the [Apache License 2.0](LICENSE).

## Build Phases

| Phase | Weeks | Status | Focus |
|-------|-------|--------|-------|
| **Phase 0** | 1–4 | ✅ Complete | Core Loop Proof (graph model, scoring, state machine, CLI) |
| **Phase 1** | 5–12 | ✅ Complete | Single-User System (gateway, email, triggers, skills, inbound, infra) |
| **Phase 2** | 13–20 | Next | Multi-Channel + Organizations (WhatsApp, Telegram, org workspaces) |
| Phase 3 | 21–28 | Planned | Multi-User + Security (OAuth 2.0, JWT, IAM, A2A delegation) |
| Phase 4 | 29–36 | Planned | Visual Interface + Advanced Skills (React UI, calendar, import) |
| Phase 5 | 37–48 | Planned | Enterprise + Observability (Slack, Teams, GDPR, SOC 2) |

See [`docs/skills-and-agents-roadmap.md`](../skills-and-agents-roadmap.md) for the full skills/agents/channels roadmap.

## Contributing

Contributions are welcome! Please read [`CONTRIBUTING.md`](../governance/documentation-governance.md) and follow the code style conventions.

- **License:** [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- **Issues:** Use GitHub Issues for bugs and feature requests
- **PRs:** Target the `main` branch; include tests; ensure `pytest tests/` passes

## Build System

This project is built using the **Claude Code multi-agent system**:

- **Opus** — Architecture decisions, planning, complex reasoning, code review
- **Sonnet** — Code generation, implementation, testing, refactoring
- **Haiku** — Quick lookups, formatting, simple edits

See `.claude/` for agent definitions and custom skills used during the build.

## Local Build Deployment and Testing

### Running the pgadmin client for accessing the postgress age running in local container.
docker run -d --name pgadmin --network graphclaw-cockpit_default -e PGADMIN_DEFAULT_EMAIL=**********gmail.com -e PGADMIN_DEFAULT_PASSWORD=admin -p 5050:80 dpage/pgadmin4

#### To log into the home page
http://localhost:5050/login
Login to pdAdmin: abhishekgupta86@gmail.com/admin
connect to database: 
Host: db
Port: 5432
Username: graphclaw
Database: graphclaw
Password: graphclaw_dev

### To Login into Minio for the object store inspection on local
http://localhost:9001/login
user id: graphclaw/ graphclaw_dev



## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
