# Edge Creation Rules

Use these rules to decide which edge type to create between nodes.

## Task-to-Task Edges

| Edge | Direction | When to create |
|------|-----------|---------------|
| DEPENDS_ON | task → dependency | Task cannot start until the dependency is COMPLETE. |
| BLOCKS | task → blocked_task | This task is actively preventing another from progressing. |
| PART_OF | task → goal | This task is a sub-task of a goal or composite task. |
| SPAWNED_FROM | new_task → original | New task was created as a result of working on the original. |
| FOLLOW_UP_FOR | follow_up → original | This follow-up tracks an action required after the original. |
| BRANCHED_FROM | variant → original | Alternative approach branched from an existing task. |
| BATCHED_IN | task → checkin | Task is grouped into a check-in or batch review. |

## Task-to-Entity Edges

| Edge | Direction | When to create |
|------|-----------|---------------|
| ASSIGNED_TO | task → user | Task is assigned to a specific user or agent. |
| OWNED_BY | task → user | Task is owned by a user (set on creation). |
| APPLIES_TO | task → resource | Task is about or modifies a resource. |
| INFORMS | task → goal | Completing this task provides information relevant to the goal. |

## Rules
- Always create OWNED_BY when creating a task (points to the user who created it).
- Create ASSIGNED_TO when assigning to a different person than the owner.
- Create PART_OF when the task belongs under a goal or composite task.
- Create DEPENDS_ON when sequential ordering is required.
