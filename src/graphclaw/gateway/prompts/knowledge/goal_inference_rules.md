# Goal Inference and Node Retrieval Rules

## Bottom-Up Goal Inference
When the user describes work without stating a goal:
1. Identify the implied outcome: "What does this work achieve?"
2. Create a GOAL with that outcome as the title.
3. Create the described work as ATOMIC or COMPOSITE tasks under that goal via PART_OF.

Example: "I need to send a follow-up email to John about the contract" →
- Infer goal: "Close contract with John"
- Create FOLLOW_UP task: "Follow up with John about contract"
- Wire task to goal via PART_OF

## Node Retrieval Strategy
Follow this order to avoid loading unnecessary data:

1. **Start at the goal level**
   - Fetch active GoalNode summaries for the user.
   - The system prompt already shows the top active goals.
   - Do NOT load all tasks speculatively.

2. **Expand to tasks on demand**
   - When the user references a specific goal: `list_tasks(goal_id=GOAL-xxx)`
   - When planning: `list_tasks(goal_id=GOAL-xxx)` to see what already exists.
   - When briefing: `list_tasks(limit=5)` for the top priority tasks only.

3. **Skip completed goals**
   - A goal with state=COMPLETE is irrelevant unless the user explicitly asks about it.
   - `list_tasks(include_completed=false)` is the default — do not change this unless asked.

4. **Use get_task_details for drill-down**
   - When the user asks about a specific task: `get_task_details(node_id=TSK-xxx)`
   - This returns edges (dependencies, blockers, parent goal) in one call.

## When to Read Knowledge
- Before creating any node: `read_knowledge("node_creation_rules")`
- Before creating any edge: `read_knowledge("edge_creation_rules")`
- Before transitioning a task state: `read_knowledge("state_machine_rules")`
- Before transitioning a goal state (ON_HOLD, COMPLETE, OBSOLETE): `read_knowledge("goal_lifecycle_rules")`
- When discussing priorities: `read_knowledge("scoring_context")`
- When discussing follow-ups: `read_knowledge("follow_up_timing")`
- Before creating or updating a ConstraintNode: `read_knowledge("constraint_rules")`
- Before assigning a task to a person or agent, or creating a ResourceNode: `read_knowledge("resource_rules")`
