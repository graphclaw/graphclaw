---
agent: ws-j-inbound-protocol
model: sonnet
phase: 1
workstream: WS-J
depends_on: [WS-F, WS-G, WS-I, WS-A]
skills:
  - inbound-protocol-patterns
  - age-cypher-patterns
  - graphclaw-scoring-algorithm
  - graphclaw-state-machine
  - graphclaw-test-patterns
---

# WS-J: Inbound Update Protocol Agent

## Role
Implement the 6-step inbound update protocol: task resolution (ID + embedding),
status signal extraction, follow-up evaluation, graph cascade, and action routing.

## Responsibilities
- Task ID extraction (regex: TSK-[A-Z]{2}-\d{4,}-[A-Z]{3,})
- Vector embedding search fallback (pgvector cosine similarity)
- Status signal extraction and confidence assessment (LLM-assisted)
- Follow-up child decision tree (COMPLETE/IN_PROGRESS/BLOCKED/DELAYED/NEEDS_INPUT)
- Graph cascade propagation (AND/OR gate check, dependency unblocking)
- Action routing (score > threshold → immediate alert, else → briefing queue)
- Embedding construction and indexing for task nodes

## Deliverables
- `src/graphclaw/inbound/__init__.py`
- `src/graphclaw/inbound/resolver.py` — Task ID + embedding resolution
- `src/graphclaw/inbound/signals.py` — Status signal extraction + confidence
- `src/graphclaw/inbound/followup.py` — Follow-up child decision tree
- `src/graphclaw/inbound/cascade.py` — Graph cascade propagation
- `src/graphclaw/inbound/router.py` — Action decision routing
- `src/graphclaw/inbound/embeddings.py` — Embedding construction + search
- `tests/test_inbound/test_resolver.py` — ID + embedding resolution tests
- `tests/test_inbound/test_signals.py` — Signal extraction tests
- `tests/test_inbound/test_cascade.py` — Cascade propagation tests

## Key Patterns
- Pipeline pattern: each step returns context for the next
- pgvector <=> operator for cosine distance
- Confidence thresholds: >0.85 match, 0.70-0.85 low confidence, <0.70 no match
- Graph traversal via existing GraphRepository + AGE Cypher

## Constraints
- Embedding model: use same provider as skill agents (via LiteLLM)
- Low-confidence matches flagged for human confirmation in next briefing
- Cascade must invalidate scoring cache for affected nodes
- All DB operations via GraphRepository (no raw SQL)
