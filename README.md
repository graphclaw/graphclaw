# GraphClaw — Graph-Based Task Orchestration System

**Domain:** [graphclaw.ai](https://graphclaw.ai)
**Vision:** OpenClaw — autonomous agents on a secure, open, modular architecture

GraphClaw is a graph-based task orchestration system where an AI agent manages tasks for humans and other agents via a property graph. Tasks, goals, constraints, and resources are modeled as graph nodes connected by typed edges (dependencies, assignments, blocking relationships). A 7-factor scoring algorithm continuously prioritizes the action queue, while a state machine enforces lifecycle invariants.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   CLI (Typer + Rich)                │
│  task list/show/create │ agent run/score/briefing   │
├─────────────────────────────────────────────────────┤
│              Agent Reasoning Loop                   │
│  fetch tasks → build context → score → action queue │
├─────────────┬────────────────┬──────────────────────┤
│ State       │ Scoring Engine │ Chain Topology       │
│ Machine     │ (7 factors)    │ (sequential/parallel)│
├─────────────┴────────────────┴──────────────────────┤
│          Domain Models (Pydantic v2)                │
│  TaskNode · GoalNode · UserNode · Edges · Scoring   │
├─────────────────────────────────────────────────────┤
│       Database (Postgres + Apache AGE + pgvector)   │
│  GraphRepository · Cypher Queries · Embeddings      │
└─────────────────────────────────────────────────────┘
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| Graph DB | PostgreSQL + [Apache AGE](https://age.apache.org/) (Cypher queries) |
| Vectors | [pgvector](https://github.com/pgvector/pgvector) (embedding similarity) |
| API | FastAPI (planned Phase 1) |
| AI Orchestration | Anthropic SDK |
| Multi-provider | LiteLLM (planned Phase 2) |
| Validation | Pydantic v2 |
| CLI | Typer + Rich |
| Infrastructure | Docker Compose |
| Storage | MinIO (S3-compatible, planned) |
| Caching | Redis (planned) |

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.10+

### 1. Start the database

```bash
cd docker
cp .env.example .env
docker compose up -d db
```

This builds a custom Postgres image with Apache AGE and pgvector, then runs `init-db.sql` to create the graph schema (15 node labels, 8 edge labels, embedding table).

### 2. (Optional) Load seed data

```bash
docker compose exec db psql -U graphclaw -d graphclaw -f /scripts/seed-data.sql
```

### 3. Install the project

```bash
pip install -e ".[dev]"
```

### 4. Run tests

```bash
# Unit tests (no DB required)
pytest tests/ -m "not integration"

# Integration tests (requires running DB)
export TEST_DATABASE_URL=postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw_test
pytest tests/test_db/ -m integration
```

### 5. Use the CLI

```bash
# Show help
graphclaw --help

# Score tasks and show action queue
graphclaw agent score

# Generate a briefing
graphclaw agent briefing

# Run one agent reasoning cycle
graphclaw agent run
```

### 6. Build the app container

```bash
cd docker
docker compose up -d
```

## Project Structure

```
openclawdotai/
├── .claude/                    # Claude Code configuration & skills
│   ├── skills/                 # 7 custom skills for domain patterns
│   │   ├── age-cypher-patterns/
│   │   ├── graphclaw-pydantic-schemas/
│   │   ├── graphclaw-scoring-algorithm/
│   │   ├── graphclaw-state-machine/
│   │   ├── graphclaw-docker-dev/
│   │   ├── graphclaw-test-patterns/
│   │   └── graphclaw-cli-patterns/
│   └── agents/                 # Agent definitions used in multi-agent build
├── src/graphclaw/
│   ├── models/                 # Pydantic domain models
│   │   ├── enums.py            # 16 enumerations
│   │   ├── base.py             # BaseNode, ID generators
│   │   ├── nodes.py            # TaskNode, GoalNode, UserNode, etc.
│   │   ├── edges.py            # GraphEdge with typed properties
│   │   ├── type_metadata.py    # Discriminated union per task type
│   │   └── scoring.py          # ScoreFactor, ScoreExplanation, ActionQueueEntry
│   ├── db/                     # Database layer
│   │   ├── connection.py       # Async psycopg pool with AGE setup
│   │   ├── graph_repository.py # Node/edge CRUD operations
│   │   └── queries/            # Critical path, dependencies, scoring
│   ├── state/                  # State machine
│   │   ├── transitions.py      # Valid transition table (10 states)
│   │   ├── machine.py          # StateMachine with guards
│   │   └── cascade.py          # Composite completion cascade
│   ├── scoring/                # 7-factor scoring engine
│   │   ├── factors/            # Pure scoring functions (7 files)
│   │   ├── engine.py           # ScoringEngine, ScoringContext
│   │   ├── topology.py         # Chain analysis, sequential suppression
│   │   ├── cache.py            # ScoreCache with invalidation triggers
│   │   └── action_queue.py     # ActionQueueEntry builder
│   ├── agent/                  # Agent reasoning loop
│   │   ├── loop.py             # AgentLoop: fetch → score → queue
│   │   └── briefing.py         # Human-readable briefing generator
│   └── cli/                    # CLI interface
│       ├── main.py             # Typer app with sub-commands
│       ├── task_commands.py    # task list/show/create/transition
│       ├── goal_commands.py    # goal list/show
│       ├── graph_commands.py   # graph stats/query
│       ├── agent_commands.py   # agent run/score/briefing
│       └── formatters.py       # Rich formatting utilities
├── tests/                      # pytest test suite
│   ├── test_models/            # 57 model validation tests
│   ├── test_state/             # 41 state machine + cascade tests
│   ├── test_scoring/           # 52 factor + engine tests
│   ├── test_agent/             # 22 agent loop tests
│   ├── test_cli/               # 25 CLI command tests
│   └── test_db/                # 15 integration tests (requires DB)
├── docker/
│   ├── Dockerfile              # App container (Python 3.12-slim)
│   ├── Dockerfile.db           # DB container (AGE + pgvector)
│   └── docker-compose.yml      # Full local dev stack
├── scripts/
│   ├── init-db.sql             # Graph schema DDL
│   └── seed-data.sql           # Sample data (6 tasks, dependencies)
├── CLAUDE.md                   # Claude Code project instructions
├── build-plan.md               # 6-phase implementation plan
├── task-graph-requirements.md  # PRD v1.1 (8500+ lines)
└── task-graph-review-notes.md  # Design review observations
```

## Scoring Algorithm

The 7-factor weighted scoring formula prioritizes tasks:

| # | Factor | Weight | Description |
|---|--------|--------|-------------|
| W1 | Timeline Urgency | 0.25 | Days to deadline, effort slack |
| W2 | Dependency Weight | 0.20 | Direct + transitive downstream dependents |
| W3 | Critical Path | 0.20 | On critical path × goal priority multiplier |
| W4 | Blocker Score | 0.15 | Hard (1.0) / Soft (0.6) blocker elevation |
| W5 | Human Override | 0.10 | Prioritize (+1.0) / Deprioritize (-0.3) / Snooze (exclude) |
| W6 | Resource Risk | 0.05 | Reliability, load, risk signals |
| W7 | Constraint Pressure | 0.05 | Budget/time/resource constraint proximity |

**Post-multipliers:** Critical path P1 goal = 1.5×, P2 = 1.3×, P3 = 1.1×
**Chain topology:** Sequential chains suppress non-first nodes; urgency rolls up to chain head

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

Special: COMPLETE → NEEDS_REVIEW (low-confidence reopen only)
```

**Guards:** Approval tasks require human to complete · INACTIVE_PENDING activation requires CASCADE/HUMAN/SYSTEM · Terminal states are absolute (except low-confidence reopen)

## Build System

This project is built entirely using the **Claude Code multi-agent system**:

- **Opus** — Architecture decisions, planning, complex reasoning, code review
- **Sonnet** — Implementation, code generation, testing, refactoring
- **Haiku** — Quick lookups, formatting, simple edits

See `.claude/agents/` for the agent definitions used during the Phase 0 build.

## Build Phases

| Phase | Weeks | Focus |
|-------|-------|-------|
| **Phase 0** ✅ | 1–4 | Core Loop Proof (graph model, scoring, state machine, CLI) |
| Phase 1 | 5–12 | Single-User System (API, channels, NL task creation, persistence) |
| Phase 2 | 13–20 | Skill Agent Layer (research, email, calendar, delegation) |
| Phase 3 | 21–28 | Multi-User + Delegation + Security (OAuth, JWT, IAM) |
| Phase 4 | 29–36 | Production Hardening (rate limits, circuit breakers, RBAC) |
| Phase 5 | 37–48 | Scale, Observability, Enterprise (CloudWatch, alerting, backups) |

## License

Proprietary — All rights reserved.
