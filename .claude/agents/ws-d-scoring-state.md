---
agent: ws-d-scoring-state
model: sonnet
phase: 0
workstream: WS-D
depends_on: [WS-A, WS-B]
skills:
  - graphclaw-scoring-algorithm
  - graphclaw-state-machine
  - graphclaw-test-patterns
---

# WS-D: Scoring Engine + State Machine Agent

## Role
Implement the 7-factor scoring engine and task lifecycle state machine.

## Responsibilities

### State Machine
- Valid transition table (10 states with typed transitions)
- StateMachine class with guards:
  - Terminal state enforcement (CANCELLED absolute, COMPLETE with low-confidence exception)
  - Approval tasks require human to complete
  - INACTIVE_PENDING/BLOCKED activation requires CASCADE/HUMAN/SYSTEM
- State history recording (StateHistoryEntry on every transition)
- Composite completion cascade (AND/OR gates, confidence halting, approval blocking)
- Sequential chain activation (INACTIVE_PENDING → ACTIVE on predecessor completion)

### Scoring Engine
- 7 pure factor functions (timeline, dependency, critical path, blocker, override, resource risk, constraint)
- ScoringEngine with configurable weights (W1=0.25..W7=0.05)
- Critical path post-multiplier (P1=1.5×, P2=1.3×, P3=1.1×)
- Chain topology analysis (sequential suppression, urgency rollup)
- ScoreCache with 6 invalidation triggers
- Action queue builder with recommended actions

## Deliverables
- `src/graphclaw/state/transitions.py`, `machine.py`, `cascade.py`
- `src/graphclaw/scoring/factors/` (7 factor modules)
- `src/graphclaw/scoring/engine.py`, `topology.py`, `cache.py`, `action_queue.py`
- `tests/test_state/test_machine.py`, `test_cascade.py`
- `tests/test_scoring/test_factors.py`, `test_engine.py`

## Key Patterns
- Scoring factors are pure functions (no DB, no side effects)
- ScoringEngine takes ScoringContext (pre-computed graph data)
- State machine operates on TaskNode objects in-place
- Cascade uses module-level StateMachine instance
