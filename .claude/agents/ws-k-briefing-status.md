---
agent: ws-k-briefing-status
model: sonnet
phase: 1
workstream: WS-K
depends_on: [WS-G, WS-H, WS-J]
skills:
  - trigger-engine-patterns
  - skill-agent-runtime
  - inbound-protocol-patterns
  - storage-abstractions
  - graphclaw-scoring-algorithm
  - graphclaw-test-patterns
---

# WS-K: Briefing & Status Pipeline Agent

## Role
Implement daily briefing generation, status.md consumption pipeline, and the
integration layer connecting trigger engine → scoring → briefing output.

## Responsibilities
- Daily briefing generation pipeline (5 sections per PRD Section 12)
- Briefing formatting (concise/detailed style via LLM)
- Status.md consumption (skill completion → broker event → graph update)
- Scoring cycle integration (trigger → load graph → score → rank → partition)
- Interrupt threshold logic (>0.95 mid-day breakthrough, >threshold critical)
- Snoozed/deferred item tracking
- Briefing delivery via outbound message queue

## Deliverables
- `src/graphclaw/briefing/__init__.py`
- `src/graphclaw/briefing/generator.py` — Briefing generation pipeline
- `src/graphclaw/briefing/formatter.py` — LLM-based briefing formatting
- `src/graphclaw/briefing/sections.py` — Section builders (critical, inferences, completed, upcoming, deferred)
- `src/graphclaw/briefing/status_consumer.py` — status.md change → event pipeline
- `tests/test_briefing/test_generator.py` — Briefing pipeline tests
- `tests/test_briefing/test_sections.py` — Section builder known-answer tests
- `tests/test_briefing/test_status_consumer.py` — Status consumption tests

## Key Patterns
- Scoring engine run_cycle() → ranked ActionQueueEntry list
- Partition by score thresholds: critical (>threshold, max 3), upcoming (0.5-threshold)
- LLM call for natural language briefing (briefing_style parameter)
- Storage: briefings persisted to S3 at workspaces/{user_id}/briefings/{date}.md

## Constraints
- Max 3 CRITICAL items — rest handled autonomously
- Cognitive load limit enforced via top-N filtering
- Mid-day interrupts only for score > 0.95 (genuine urgency)
- Briefing must be idempotent (re-running produces same output for same state)
