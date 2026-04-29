# Constraint Rules

## When to Create a ConstraintNode

Create a ConstraintNode when the user expresses a limit, requirement, or pressure that
applies across tasks or goals — not to a single task's timeline field.

| Type | When to create |
|------|---------------|
| DEADLINE | Hard external date not owned by a single task (legal filing deadline, board presentation). |
| BUDGET | A spend cap or funding limit for a goal or workspace. |
| COMPLIANCE | A regulatory, legal, or policy requirement that restricts how work is done. |
| EXTERNAL | A dependency on a third-party event or party outside the user's control. |
| DEPENDENCY | A structural ordering constraint spanning multiple goals or workstreams. |
| CAPACITY | A ceiling on how much work a resource or team can absorb concurrently. |
| CUSTOM | Any other named pressure the user explicitly declares as a governing rule. |

Do NOT create a ConstraintNode for a task's own deadline — that belongs in
`timeline.deadline` on the TaskNode itself.

## Scope

| Scope | Applies to |
|-------|-----------|
| TASK | A single task node. Use sparingly — prefer the task's own timeline. |
| MILESTONE | All tasks under a milestone. |
| GOAL | All tasks under a goal. |
| GLOBAL | All active tasks for the user. |

## Key Fields to Set

- `rule.hard_limit` — the ceiling or date that must not be crossed.
- `rule.threshold` — the warning level (e.g. 80% of budget = threshold; 100% = hard_limit).
- `rule.current_value` — current measured value (updated by the agent as tasks complete or costs accrue).
- `rule.pressure_score` — 0.0–1.0; how close current_value is to the threshold. You compute this.
- `rule.breached` — set to true when current_value exceeds hard_limit.
- `applies_to` — list the node IDs this constraint governs.

## APPLIES_TO Edge

Always create an `APPLIES_TO` edge from the ConstraintNode to every node it governs
(tasks, milestones, or goals listed in `applies_to`).

## Agent-Inferred Constraints

- When you infer a constraint (not explicitly stated by the user), set `origin=AGENT_INFERRED`
  and `confirmed_by_user=false`.
- State the inferred constraint to the user before creating it: "I noticed a hard deadline
  of [date] — should I register this as a constraint on the [goal]?"
- Never transition `confirmed_by_user` to true without explicit user acknowledgement.

## Constraint Pressure and W7

Constraint pressure feeds directly into the W7 scoring factor for every task in scope:

- `pressure_score < 0.5` — low pressure; W7 contribution is minimal.
- `0.5 ≤ pressure_score < 0.8` — moderate; surface to user in briefings.
- `pressure_score ≥ 0.8` — high; elevate all tasks in scope, mention in action queue.
- `breached = true` — flag immediately; do not wait for the next scheduled briefing.

## When to Update a ConstraintNode

- When the user changes a deadline, budget, or cap: update `rule.hard_limit` and recompute `pressure_score`.
- When a compliance requirement is lifted or fulfilled: transition the constraint's `applies_to`
  list to empty and note it in the goal's `intelligence` field.
- When a constraint is no longer relevant: remove the `APPLIES_TO` edges and archive the node
  (do not delete — retain for audit).
