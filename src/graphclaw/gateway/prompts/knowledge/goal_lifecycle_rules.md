# Goal Lifecycle Rules

## Valid Goal State Transitions

```
ACTIVE → COMPLETE     (all milestones done or user explicitly closes the goal)
ACTIVE → ON_HOLD      (work paused; goal is still valid)
ACTIVE → OBSOLETE     (goal is no longer relevant; work will not resume)
ON_HOLD → ACTIVE      (work resumes)
ON_HOLD → OBSOLETE    (decision made to abandon the goal while paused)
```

COMPLETE and OBSOLETE are terminal — no transitions out.

## Transition Guards

| Transition | Guard |
|------------|-------|
| ACTIVE → COMPLETE | All milestone tasks under the goal must be COMPLETE, OR the user explicitly says the goal is done. Never auto-complete without milestone rollup confirmation. |
| ACTIVE → OBSOLETE | Only on explicit user instruction. Always confirm: "This will mark [goal] as obsolete — are you sure?" |
| ON_HOLD → OBSOLETE | Only on explicit user instruction. Apply the same confirmation. |
| ACTIVE → ON_HOLD | No task-level guard. Set immediately when user says "pause this" or "put this on hold". |

## Milestone Completion Rollup

When a MILESTONE task transitions to COMPLETE:
1. Increment `goal.progress.milestones_done` by 1.
2. Recompute `goal.progress.derived_percentage` = `milestones_done / milestone_count * 100`.
3. If `milestones_done == milestone_count`, surface to the user:
   "All milestones under [goal] are complete — should I mark the goal as COMPLETE?"
4. Wait for explicit confirmation before transitioning to COMPLETE.

If the goal has no MILESTONE tasks, use task COMPLETE count as a proxy for progress but
still require user confirmation before closing the goal.

## Agent-Inferred Goals

Goals created by the agent (not explicitly stated by the user) have `origin=AGENT_INFERRED`
and `confirmed_by_user=false`.

- Never transition an unconfirmed inferred goal to OBSOLETE without user input.
- Never surface an unconfirmed goal in the action queue as if it were a committed objective.
- On the next interaction after inferring a goal, confirm: "I created a goal [title] from your
  earlier request — does that look right?"
- Once the user acknowledges, set `confirmed_by_user=true`.

## ON_HOLD Behaviour

- Tasks under an ON_HOLD goal should not appear in the default action queue.
- Filter them from `list_tasks` by default (use `include_on_hold=false`).
- Surface them only when the user asks about paused work or when resuming.
- Do not automatically transition tasks to SNOOZED — the goal's state is the gate.

## OBSOLETE Behaviour

- Tasks under an OBSOLETE goal should be CANCELLED (not COMPLETE).
- Transition each open task to CANCELLED before or immediately after marking the goal OBSOLETE.
- Preserve all completed tasks as-is for historical record.
- Never delete nodes — retain for audit and reuse.

## Goal Intelligence Field

Use `goal.intelligence` as a running markdown log for non-obvious events:
- When a goal transitions state, append a timestamped entry: `[date] → ON_HOLD: user paused for Q3 budget freeze`.
- When a constraint is applied to a goal, note it here.
- When the agent infers a goal, note the originating user statement here.

Do NOT duplicate information already in `state_history` — only add entries that explain WHY,
not just WHAT changed.

## When to Read Goal State

- Before suggesting a goal completion: verify milestone rollup via `get_task_details(goal_id)`.
- Before marking obsolete: read `goal.intelligence` for context on why the goal was created.
- When briefing: show ON_HOLD goals separately from ACTIVE goals. Never mix them.
