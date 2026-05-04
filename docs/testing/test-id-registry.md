# Test ID Registry — graphclaw

> Auto-generated from `tests/*/inventory.md` files by `scripts/regen_inventory.py`. Do not edit manually — changes will be overwritten.
>
> To add an ID: write the test file with a valid header, then run `python scripts/regen_inventory.py`.

| ID | Scenario | Layer | File |
|---|---|---|---|
| GC-A-ORC-W12-001 | Orchestrator creates a task from chat input | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/001_create_task_basic.yaml` |
| GC-A-ORC-W12-002 | Orchestrator delegates email-drafting to email skill | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/002_delegate_to_skill.yaml` |
| GC-A-ORC-W12-003 | Orchestrator produces correct follow-up after inbound reply | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/003_followup_after_inbound.yaml` |
| GC-A-ORC-W12-004 | Orchestrator reasons about task priority correctly | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/004_priority_reasoning.yaml` |
| GC-A-ORC-W12-005 | Orchestrator enters clarification loop on ambiguous intent | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/005_clarification_loop.yaml` |
| GC-A-ORC-W12-006 | Orchestrator refuses out-of-scope requests gracefully | L7 Agent Eval | `tests/agent_evals/prompts/orchestrator/006_refuse_out_of_scope.yaml` |

_Last regenerated: 2026-05-04 (stub — will be populated by scripts/regen_inventory.py in Phase 3)_
