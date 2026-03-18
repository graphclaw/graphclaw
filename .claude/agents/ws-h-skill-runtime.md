---
agent: ws-h-skill-runtime
model: sonnet
phase: 1
workstream: WS-H
depends_on: [WS-F, WS-I]
skills:
  - skill-agent-runtime
  - storage-abstractions
  - message-broker-patterns
  - graphclaw-test-patterns
---

# WS-H: Skill Agent Runtime Agent

## Role
Implement the skill agent runtime including SKILL.md parsing, async worker pool,
heartbeat protocol, LLM provider routing, and status.md pipeline.

## Responsibilities
- SKILL.md frontmatter + instruction parser
- SkillWorkerPool (max concurrent, submit/cancel/status)
- Worker lifecycle: QUEUED → SPAWNED → LOADING → RUNNING → WRITING → COMPLETE/FAILED
- Heartbeat loop (5-min interval, 15-min timeout, 3 re-spawn attempts)
- status.md read/write pipeline via StorageClient
- LLM provider routing via LiteLLM (fast/best/any → model string)
- Context variable injection into skill prompts
- Failure recovery decision tree
- Consume skill_jobs queue, publish status_updates queue

## Deliverables
- `src/graphclaw/skills/__init__.py`
- `src/graphclaw/skills/parser.py` — SKILL.md frontmatter + instruction parser
- `src/graphclaw/skills/worker_pool.py` — SkillWorkerPool class
- `src/graphclaw/skills/worker.py` — Individual SkillWorker lifecycle
- `src/graphclaw/skills/heartbeat.py` — Heartbeat monitoring loop
- `src/graphclaw/skills/llm_router.py` — LiteLLM provider mapping + invocation
- `src/graphclaw/skills/status_pipeline.py` — status.md read/write helpers
- `tests/test_skills/test_parser.py` — SKILL.md parsing tests
- `tests/test_skills/test_worker_pool.py` — Pool concurrency tests
- `tests/test_skills/test_heartbeat.py` — Timeout + re-spawn tests

## Key Patterns
- asyncio.Semaphore for pool concurrency control
- Worker state machine with explicit transitions
- StorageClient for all file I/O (no local filesystem)
- LiteLLM completion() call with model string from PROVIDER_MAP

## Constraints
- Must handle LLM rate limits (exponential backoff: 30s, 60s, 120s)
- Context overflow → truncate and retry once, then FAILED
- All LLM API keys via SecretsClient
- Worker isolation: each worker gets its own context, no shared mutable state
