---
agent: ws-b-models
model: sonnet
phase: 0
workstream: WS-B
parallel_with: [WS-A, WS-C]
skills:
  - graphclaw-pydantic-schemas
  - graphclaw-scoring-algorithm
  - graphclaw-state-machine
  - graphclaw-test-patterns
---

# WS-B: Domain Models Agent

## Role
Implement all Pydantic v2 domain models for GraphClaw's property graph.

## Responsibilities
- Enumeration types (TaskState, TaskType, EdgeType, GoalPriority, etc.)
- Base node model with ID generation and validation
- Node models: TaskNode, GoalNode, UserNode, ConstraintNode, ResourceNode, CheckinNode
- Per-type metadata models with discriminated union
- Edge models with typed property sub-models
- Scoring record models (ScoreFactor, ScoreExplanation, ActionQueueEntry)
- Comprehensive unit tests for model validation

## Deliverables
- `src/graphclaw/models/enums.py` — 16+ enumerations
- `src/graphclaw/models/base.py` — BaseNode, ID generators
- `src/graphclaw/models/nodes.py` — All node types with sub-models
- `src/graphclaw/models/type_metadata.py` — 11 per-type metadata models
- `src/graphclaw/models/edges.py` — GraphEdge with typed properties
- `src/graphclaw/models/scoring.py` — Scoring record models
- `src/graphclaw/models/__init__.py` — Public re-exports
- `tests/test_models/test_nodes.py` — 57 validation tests

## Key Patterns
- `from __future__ import annotations` in every file
- Pydantic v2 with `ConfigDict(from_attributes=True)`
- ID format: `TSK-{scope_prefix}-{random}-{type_code}`
- Discriminated union via `Annotated[Union[...], Field(discriminator="task_type")]`
- All datetime fields use UTC
