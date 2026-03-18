---
agent: ws-g-trigger-engine
model: sonnet
phase: 1
workstream: WS-G
parallel_with: [WS-F, WS-I]
depends_on: [WS-A, WS-B, WS-D]
skills:
  - trigger-engine-patterns
  - message-broker-patterns
  - graphclaw-scoring-algorithm
  - graphclaw-test-patterns
---

# WS-G: Trigger Engine Agent

## Role
Implement the trigger engine that handles time-based, event-based, inbound,
and on-demand triggers with follow-up timing computation.

## Responsibilities
- TriggerEngine main loop (scheduled + event consumer coroutines)
- Time-based trigger scheduling (cron-like, daily briefing at user pref time)
- Event-based triggers (broker messages → TriggerEvent dispatch)
- Inbound trigger routing (inbound_messages → trigger_events)
- On-demand trigger API endpoint
- Follow-up timing model (base_cadence × complexity × reliability × recency)
- Trigger persistence (due triggers, fire history)

## Deliverables
- `src/graphclaw/triggers/__init__.py`
- `src/graphclaw/triggers/engine.py` — TriggerEngine class
- `src/graphclaw/triggers/scheduler.py` — Cron-like scheduled trigger checker
- `src/graphclaw/triggers/models.py` — TriggerEvent, TriggerConfig Pydantic models
- `src/graphclaw/triggers/followup.py` — Follow-up timing computation
- `tests/test_triggers/test_engine.py` — Engine loop tests
- `tests/test_triggers/test_scheduler.py` — Scheduling logic tests
- `tests/test_triggers/test_followup.py` — Timing model known-answer tests

## Key Patterns
- asyncio.create_task for parallel scheduled + event loops
- Broker consume loop with graceful shutdown (cancellation)
- Follow-up timing formula from PRD Section 10
- Trigger deduplication (idempotency key per trigger)

## Constraints
- DB access for trigger persistence and user preferences
- Scoring engine integration for priority-based trigger ordering
- Must not block on any single trigger dispatch
