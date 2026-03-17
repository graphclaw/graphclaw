---
name: graphclaw-state-machine
description: Task lifecycle state machine with valid transitions, guards, cascade logic, and history recording from PRD Section 7. Use when implementing state transition logic or cascade behavior.
---

# GraphClaw Task State Machine

## Valid Transitions

```python
VALID_TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.PENDING:          [TaskState.ACTIVE, TaskState.CANCELLED, TaskState.SNOOZED, TaskState.INACTIVE_PENDING],
    TaskState.ACTIVE:           [TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.DELAYED, TaskState.NEEDS_REVIEW, TaskState.CANCELLED, TaskState.SNOOZED],
    TaskState.IN_PROGRESS:      [TaskState.COMPLETE, TaskState.BLOCKED, TaskState.DELAYED, TaskState.NEEDS_REVIEW, TaskState.CANCELLED],
    TaskState.BLOCKED:          [TaskState.ACTIVE],
    TaskState.DELAYED:          [TaskState.IN_PROGRESS],
    TaskState.NEEDS_REVIEW:     [TaskState.IN_PROGRESS, TaskState.COMPLETE],
    TaskState.SNOOZED:          [TaskState.ACTIVE],
    TaskState.INACTIVE_PENDING: [TaskState.ACTIVE],
    TaskState.COMPLETE:         [],        # Terminal (except NEEDS_REVIEW reopen)
    TaskState.CANCELLED:        [],        # Terminal
}
```

## Guards

1. **CANCELLED is terminal** — no transitions out
2. **COMPLETE is terminal** — except agent can reopen to NEEDS_REVIEW if confidence is LOW
3. **Approval tasks cannot be auto-resolved** — must be completed by a human
4. **INACTIVE_PENDING → ACTIVE** only when predecessor task completes
5. **BLOCKED → ACTIVE** only when blocker is resolved

## State History Recording

Every transition MUST record:

```python
StateHistoryEntry(
    from_state=current_state,
    to_state=new_state,
    changed_at=datetime.utcnow(),
    changed_by="AGENT" | "HUMAN" | "SYSTEM" | "CASCADE",
    reason="description of why transition occurred"
)
```

## Composite Completion Cascade (Section 7.2)

When a child task reaches COMPLETE:

```python
def check_composite_completion(parent_task, children):
    # 1. Check remaining children
    incomplete = [c for c in children if c.state != TaskState.COMPLETE]

    # 2. Check open Review/Approval children
    pending_reviews = [c for c in incomplete
                       if c.task_type in (TaskType.REVIEW, TaskType.APPROVAL)]
    if pending_reviews:
        return  # Cannot auto-complete — wait for human

    # 3. Check gate type
    if parent_task.type_metadata.completion_gate == GateType.AND:
        if incomplete:
            return  # Not all children complete
    elif parent_task.type_metadata.completion_gate == GateType.OR:
        pass  # At least one child complete — proceed

    # 4. Confidence check
    low_confidence = [c for c in children
                      if c.task_type in (TaskType.RESEARCH, TaskType.REVIEW)
                      and c.confidence == "LOW"]
    if low_confidence:
        parent_task.state = TaskState.NEEDS_REVIEW
        return  # Halt cascade

    # 5. Auto-complete parent
    transition(parent_task, TaskState.COMPLETE, changed_by="CASCADE")

    # 6. Recurse upward
    if parent_task has parent:
        check_composite_completion(grandparent, siblings)

    # 7. Update Goal progress if parent is under a Goal
    update_goal_progress(parent_task.goal_id)
```

## Milestone Completion

When a Milestone task completes:
- Notify the user (regardless of briefing schedule)
- Update parent Goal progress
- Check if next phase of work should be unlocked

## Sequential Chain Activation

When a task in a sequential chain completes:
- Find next task in chain (via DEPENDS_ON edges)
- Transition from INACTIVE_PENDING → ACTIVE
- Record changed_by="CASCADE"
