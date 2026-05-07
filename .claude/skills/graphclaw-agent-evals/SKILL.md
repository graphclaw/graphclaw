---
name: graphclaw-agent-evals
description: Agent evaluation patterns for GraphClaw — YAML scenario authoring, behavioral assertions, LLM-as-judge rubrics, cost budgets, and canary markers. Use when writing scenarios in tests/agent_evals/prompts/ or modifying src/agent/, src/skills/, or src/llm/.
---

# GraphClaw Agent Eval Patterns

## When to use
- Writing YAML scenarios under `tests/agent_evals/prompts/`
- Modifying `src/agent/`, `src/skills/`, or `src/llm/` (add/review canary coverage)
- Authoring or updating LLM-as-judge rubrics

---

## What agent evals guard against

Behavioral regressions that no other test layer catches:
- Orchestrator answers inline instead of delegating to a skill
- Orchestrator loses task context across tool calls
- Orchestrator loops unnecessarily instead of clarifying once
- Orchestrator refuses a legitimate request it previously handled

---

## YAML scenario schema

```yaml
# tests/agent_evals/prompts/orchestrator/NNN_title.yaml
id: GC-A-ORC-W<NN>-<NNN>         # required — allocate from agent_evals/inventory.md
title: One-line description        # required
description: |                     # optional but recommended
  2-3 sentences. What behaviour this scenario proves.
  Why regression here would be significant.

setup:
  seed_dataset: minimal_v1         # from tests/fixtures/seed_data/
  user: dev@example.com

turns:
  - user: "What the user says"
    assert:
      # Behavioral assertions — see vocabulary below
      - tool_called: skill.invoke
      - tool_args_match:
          skill_name: email-drafting
      - response_does_not_contain: ["Subject:", "Dear "]
      - latency_ms_under: 8000

  - user: "Follow-up turn"
    assert:
      - tool_called: skill.invoke
      - turn_count_under: 3

teardown:
  cleanup_session: true

rubric:                            # optional LLM-as-judge scoring
  judge_model: claude-sonnet-4-6
  rubric_file: tool_use_correctness.md
  pass_threshold: 0.8

budget:
  max_tokens: 4000
  max_cost_usd: 0.10
```

---

## Behavioral assertion vocabulary

| Assertion | Checks |
|---|---|
| `tool_called: <name>` | Agent invoked this tool/skill |
| `tool_not_called: <name>` | Agent did NOT invoke this |
| `tool_args_match: {...}` | Tool args contain expected fields (subset match) |
| `response_contains: [...]` | Response text includes all strings |
| `response_does_not_contain: [...]` | Response text includes none of these strings |
| `response_matches_regex: ...` | Response matches regex |
| `response_is_json_with_schema: <schema>` | Response is valid JSON conforming to schema |
| `latency_ms_under: N` | Turn completed in under N ms |
| `cost_usd_under: N` | Turn cost under N dollars |
| `turn_count_under: N` | Agent completed in fewer than N turns |
| `judge_score_above: 0.8` | LLM-as-judge rubric score ≥ threshold |

---

## Rubric files (`tests/agent_evals/rubrics/`)

Rubrics are markdown files. The judge model reads the rubric and scores 0–1.

```markdown
# Tool Use Correctness Rubric

Score 1.0 if:
- The agent delegated to the correct skill for the task type
- The skill arguments included all required context fields
- The agent did NOT attempt to answer directly instead of delegating

Score 0.5 if:
- The agent delegated to the correct skill but with incomplete arguments
- Minor context was missing but the delegation was fundamentally correct

Score 0.0 if:
- The agent answered directly without skill delegation
- The agent delegated to the wrong skill
- The agent refused without reasonable justification
```

---

## Canary marker

Mark 1 in 5 scenarios as canary — these run on every PR touching agent/skills/llm code:

```python
# In test_orchestrator_evals.py:
pytestmark = [pytest.mark.agent_eval, pytest.mark.slow]

# To mark individual scenarios as canary, set in the YAML:
# canary: true
```

Canary scenarios should be: cheap (< $0.05), fast (< 30s), and test the most likely regression path (skill delegation is the canonical canary).

---

## Cost budget enforcement

Each scenario MUST have a `budget` block. The runner enforces it:
- `max_tokens`: hard stop on total token usage per scenario
- `max_cost_usd`: hard stop on cost per scenario

Overall budget caps (enforced in `conftest.py`):
- Canary run (PR): $0.05 total
- Full nightly run: $0.50 total

---

## Running evals

```bash
# Canary only (PR-safe)
pytest tests/agent_evals/ -m eval_canary --run-evals

# Full suite (nightly)
pytest tests/agent_evals/ --run-evals

# Single scenario
pytest tests/agent_evals/test_orchestrator_evals.py::test_orchestrator_scenario[002_delegate_to_skill] --run-evals
```

---

## Inventory workflow

After adding a scenario YAML, add to `tests/agent_evals/inventory.md`:
```
| GC-A-ORC-W12-002 | Orchestrator delegates email-drafting to email skill | [tests/agent_evals/prompts/orchestrator/002_delegate_to_skill.yaml](../../../tests/agent_evals/prompts/orchestrator/002_delegate_to_skill.yaml) |
```

Or regenerate: `python scripts/regen_inventory.py`

---

## File header for eval test runner files (Python)

```python
"""
GC-A-ORC-W12-* — Orchestrator behavioral eval suite

Scenario: Parametrized over YAML scenarios in prompts/orchestrator/.
Proves the orchestrator delegates correctly, maintains context, and
refuses out-of-scope requests.

PRD: docs/prd/05-orchestrator.md §AC-5.*
Build wave: W12
Layer: L7 Agent Eval
Owner: agent-team
Last reviewed: YYYY-MM-DD

Cases covered:
- 001: Creates task from chat input (canary)
- 002: Delegates email-drafting to skill (canary)
- 003: Follow-up after inbound reply (canary)
- 004: Priority reasoning
- 005: Clarification loop
- 006: Refuses out-of-scope request

Notes:
- Requires --run-evals flag and ANTHROPIC_API_KEY.
- Not deterministic — consistent failure across 3 runs is signal; single failure is noise.
- Full suite costs ~$0.50; canary costs ~$0.05.
"""
```
