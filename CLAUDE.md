# GraphClaw — Task Graph Management System

## Project Context
Graph-based task orchestration system where an AI agent manages tasks for humans and other agents via a property graph.
- PRD: `docs/task-graph-requirements.md` (v1.1, 8500+ lines)
- Review notes: `docs/task-graph-review-notes.md` (28 issues across 6 categories)
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
- Local dev: `docker compose up` (SECRETS_BACKEND=env_file)

## PRD Coverage (v1.1)
- Sections 1-30: Core system (graph model, agents, channels, skills, multi-user)
- Section 31: Security, Identity & Secrets (OAuth, IAM roles, attack surface assessment)
- Section 32: Observability & Operations (structured logging, tracing, cost monitoring, alerting, backups, rolling deployments, schema migration)
- Section 33: 58 Design Principles (14 new in v1.1: security, observability, deployment)

## Current Phase
Phase 1 complete. Phase 2 (Multi-Channel + Organizations) is next. See `build-plan.md`.
