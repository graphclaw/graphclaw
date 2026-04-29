# State Machine Rules

## Valid State Transitions

```
PENDING → ACTIVE            (task is ready to start)
ACTIVE → IN_PROGRESS        (work has begun)
IN_PROGRESS → BLOCKED       (external blocker encountered)
IN_PROGRESS → DELAYED       (slipped past deadline)
IN_PROGRESS → NEEDS_REVIEW  (work done, awaiting review)
IN_PROGRESS → COMPLETE      (work done, no review needed)
NEEDS_REVIEW → COMPLETE     (review passed)
NEEDS_REVIEW → IN_PROGRESS  (revisions required)
BLOCKED → ACTIVE            (blocker resolved)
DELAYED → ACTIVE            (rescheduled and ready)
ACTIVE → CANCELLED          (no longer needed)
IN_PROGRESS → CANCELLED     (cancelled mid-flight)
ACTIVE → SNOOZED            (deferred, will resume later)
SNOOZED → ACTIVE            (resuming)
```

## Transition Guards
- BLOCKED → ACTIVE: blocker must be resolved first (update the blocking task to COMPLETE).
- PENDING → ACTIVE: parent COMPOSITE or GOAL must be ACTIVE.
- COMPLETE is terminal: no transitions out.
- CANCELLED is terminal: no transitions out.

## When to Transition
- Use `update_task_state` whenever the user reports progress or a status change.
- Include a reason when transitioning to BLOCKED, DELAYED, or CANCELLED.
- If a FOLLOW_UP task gets a response, transition to COMPLETE or spawn a new ATOMIC task.
