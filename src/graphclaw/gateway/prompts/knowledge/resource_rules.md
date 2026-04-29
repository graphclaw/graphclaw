# Resource Rules

## When to Create a ResourceNode

Create a ResourceNode the first time a named person or AI agent is referenced as
a participant in work — not just mentioned in a conversation.

| Trigger | Action |
|---------|--------|
| User says "delegate this to Sarah" | Create ResourceNode (HUMAN) for Sarah, then create ASSIGNED_TO edge. |
| User says "the vendor will handle X" | Create ResourceNode (HUMAN) for that vendor contact. |
| User configures an AI agent integration | Create ResourceNode (AI_AGENT) for that agent. |
| A task already has an `assigned_to` value with no matching ResourceNode | Create the ResourceNode before creating the edge. |

Do NOT create a ResourceNode for the primary user — they have a UserNode.

## Resource Types

| Type | When to use |
|------|------------|
| HUMAN | Any human contact: employee, vendor, external partner, contractor. |
| AI_AGENT | Any autonomous agent that receives and performs tasks. |

## Key Fields to Set on Creation

- `name` — full name or handle.
- `contact` — email, Slack handle, or other channel identifier.
- `timezone` — infer from context (mention of city, working hours); leave null if unknown.
- `capacity.max_concurrent_tasks` — default to 3 for HUMAN, 10 for AI_AGENT unless told otherwise.
- `capacity.availability_status` — default to `AVAILABLE` on creation.
- `communication_preferences.preferred_channel` — infer from how the user contacts them (email, Slack, etc.).

## ASSIGNED_TO Edge

Create an `ASSIGNED_TO` edge (task → resource) whenever a task is given to a resource.
A task can only be ASSIGNED_TO one resource at a time. If reassigned, remove the old
edge before creating the new one and note the reassignment in `task.update_log`.

## Reading Capacity and Risk

Check `capacity` and `current_risk` before assigning new tasks to a resource:

| Signal | Interpretation | Action |
|--------|---------------|--------|
| `load_factor ≥ 0.9` | Resource is near full capacity. | Warn the user before assigning; suggest deferral or reassignment. |
| `availability_status = UNAVAILABLE` | Resource is offline or on leave. | Do not assign. Surface to user with alternative options. |
| `capacity_risk ≥ 0.7` | High overload risk. | Elevate W6 score for all tasks assigned to this resource. |
| `delivery_risk ≥ 0.7` | Historically misses deadlines. | Flag when assigning deadline-sensitive tasks. |
| `responsiveness_risk ≥ 0.7` | Slow to respond. | Set a tighter follow-up schedule (use `follow_up_timing` rules). |

## CheckinNode vs FOLLOW_UP Task

Use these rules to decide which to create when you need to reach a resource:

| Situation | Create |
|-----------|--------|
| Multiple tasks need a status update from the same resource | CheckinNode (batch them via BATCHED_IN edges). |
| Single task is waiting for external action | FOLLOW_UP task (see `follow_up_timing` rules). |
| Scheduled recurring review with a resource | CHECKIN task type (not a CheckinNode). |

When creating a CheckinNode:
- Set `task_refs` to the list of task IDs being checked on.
- Create a `BATCHED_IN` edge from each task to the CheckinNode.
- Set `scheduled_for` based on the resource's `communication_preferences.batch_window_hours`.
- Set state to `SCHEDULED`; update to `SENT` once the message is dispatched.

## Updating Reliability After a Task Completes

When a task assigned to a resource transitions to COMPLETE or DELAYED:
- Prompt the user: "Should I update [resource]'s delivery record?" — do not auto-update.
- If the user confirms, increment `reliability.total_tasks_completed` or `total_tasks_delayed`.
- The reliability score is the agent's long-term memory of how this resource performs.

## Surfacing Resource Risk in Briefings

Include a resource risk note in the daily briefing when:
- Any resource has `load_factor ≥ 0.85` and has tasks due within 7 days.
- Any resource has `availability_status = UNAVAILABLE` and has open tasks assigned.
- A resource's `risk_signals` list contains any item flagged in the last 48 hours.
