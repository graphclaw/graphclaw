# Scenario Catalog — graphclaw

> Supersedes `docs/test-scenarios.md`. Updated as tests are added. IDs prefixed `TODO` will be allocated when that scenario is implemented as a test.

---

## End-to-end user scenarios (agent-level, implemented as agent evals or manual scripts)

These scenarios describe full user journeys through the orchestrator. When implemented as L7 agent evals they get a `GC-A-ORC-*` ID.

| ID | Scenario | Layer | File | Status |
|---|---|---|---|---|
| TODO-A-ORC-01 | User onboarding: sign in, configure channels, configure betty, schedule briefings | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/` | Pending |
| TODO-A-ORC-02 | User assigns follow-up task to betty; betty contacts external user via email | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/` | Pending |
| TODO-A-ORC-03 | User assigns project (birthday party); betty does work breakdown + task graph creation | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/` | Pending |
| TODO-A-ORC-04 | User assigns podcast lead-finding goal; betty plans + creates tasks + executes | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/` | Pending |

---

## Intelligence layer scenarios (inbound + outbound comms)

| ID | Scenario | Layer | File | Status |
|---|---|---|---|---|
| TODO-I-INB-01 | Inbound email reply matched to task by thread-ID (Tier 1) | L4 Integration | `tests/integration/` | Pending |
| TODO-I-INB-02 | Inbound Telegram message matched to task by vector search (Tier 3) | L4 Integration | `tests/integration/` | Pending |
| TODO-I-INB-03 | Unmatched inbound → betty asks user for direction | L4 Integration | `tests/integration/` | Pending |
| TODO-I-INB-04 | Outbound intelligence log round-trip — both outbound and inbound recorded on node | L4 Integration | `tests/integration/` | Pending |
| TODO-I-INB-05 | Log sink — structured JSONL written to MinIO, no PII in logs | L4 Integration | `tests/integration/` | Pending |

---

## Orchestrator behavioral scenarios (agent evals — implemented)

| ID | Scenario | Layer | File | Canary? | Status |
|---|---|---|---|---|---|
| GC-A-ORC-W12-001 | Orchestrator creates a task from chat input | L7 Agent Eval | `prompts/orchestrator/001_create_task_basic.yaml` | Yes | Pending impl |
| GC-A-ORC-W12-002 | Orchestrator delegates email-drafting to email skill | L7 Agent Eval | `prompts/orchestrator/002_delegate_to_skill.yaml` | Yes | Pending impl |
| GC-A-ORC-W12-003 | Orchestrator produces correct follow-up after inbound reply | L7 Agent Eval | `prompts/orchestrator/003_followup_after_inbound.yaml` | Yes | Pending impl |
| GC-A-ORC-W12-004 | Orchestrator reasons about task priority correctly | L7 Agent Eval | `prompts/orchestrator/004_priority_reasoning.yaml` | No | Pending impl |
| GC-A-ORC-W12-005 | Orchestrator enters clarification loop when intent is ambiguous | L7 Agent Eval | `prompts/orchestrator/005_clarification_loop.yaml` | No | Pending impl |
| GC-A-ORC-W12-006 | Orchestrator refuses out-of-scope requests gracefully | L7 Agent Eval | `prompts/orchestrator/006_refuse_out_of_scope.yaml` | No | Pending impl |

---

## Backend unit + integration (from existing test suite)

> IDs will be allocated during Phase 3 backfill as inventory.md files are generated. File paths are existing.

| Domain | Description | Layer | Approx count |
|---|---|---|---|
| Scoring (`SCO`) | 7-factor scoring, chain topology modifiers, cache invalidation | L1 Unit | ~13 files |
| State machine (`STA`) | Valid transitions, guards, cascade logic, history recording | L1 Unit | ~8 files |
| Inbound (`INB`) | Thread matching, entity extraction, semantic resolution | L1 Unit | ~10 files |
| Triggers (`TRG`) | Time-based, event-based, on-demand, follow-up timing | L1 Unit | ~7 files |
| Graph (`GRA`) | Node/edge CRUD, Cypher query helpers | L1 Unit + L4 Integration | ~8 files |
| Auth (`AUT`) | JWT validation, provisioning, role middleware | L1 Unit | ~12 files |
| Gateway (`GWY`) | Email/Slack/Teams/Telegram/WhatsApp adapters, rate limiting | L1 Unit | ~18 files |
| Infra (`INF`) | Broker, storage, Redis, secrets, security, observability | L1 Unit | ~26 files |
| Agent (`AGT`) | Delegation, escalation, worker pool, heartbeat | L1 Unit | ~27 files |
| API routes (`API`) | All /app/v1/ route handlers | L1 Unit | ~23 files |
| Skills (`SKL`) | Skill SKILL.md parsing, LLM router, MCP | L1 Unit | ~15 files |
| Compliance (`CMP`) | GDPR, audit, export | L1 Unit | ~12 files |
| Briefing (`BRF`) | Section builder, daily briefing generation | L1 Unit | ~varies |

---

_Last updated: 2026-05-04. Run `python scripts/regen_inventory.py` to regenerate from test headers._
