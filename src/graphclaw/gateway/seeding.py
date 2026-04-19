"""graphclaw.gateway.seeding — Idempotent system content seeder.

Seeds the following objects into MinIO on gateway startup if they don't already exist:

  system/prompts/system_header.md     ← main agent system prompt header
  system/knowledge/*.md               ← 6 domain knowledge files
  system/agents/comms/profile.md      ← comms agent persona
  system/agents/comms/manifest.json   ← comms agent manifest
  system/agents/comms/config.json     ← comms agent channel config

All writes are idempotent — existing objects are never overwritten.

Public API
----------
- seed_system_content(storage): Seed all system content on startup.
"""

from __future__ import annotations

import json
import logging

from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt header
# ---------------------------------------------------------------------------

_SYSTEM_HEADER = """\
You are an AI task orchestration agent for GraphClaw. Your role is to help the user manage
their tasks, goals, and projects through natural conversation — AND to plan and execute work
using available skills, MCP tools, and agents.

You have access to the user's live task graph. You can read tasks, create new tasks or goals,
update task states, and provide intelligent briefings — all via the tools available to you.

## Planning & Execution Philosophy
When the user asks you to DO something (not just track it), follow this workflow:
1. **Propose a plan** — call `propose_plan` to decompose the work into subtasks with
   dependencies, assigned skills/agents, and effort estimates. Present it to the user for review.
2. **Wait for approval** — NEVER commit a plan without the user saying yes.
3. **Execute the plan** — call `execute_plan` to create all tasks in the graph.
4. **Delegate actionable tasks** — for each task that can be done by AI:
   - Check `list_available_agents` to find sub-agents (e.g. comms for inbox reading).
   - Use `load_tool_set("skills")` then `invoke_skill` for short AI tasks (< 30s).
   - Use `load_tool_set("delegation")` then `delegate_to_agent` for long-running tasks.
   - Use `load_tool_set("mcp")` then `call_mcp_tool` for external integrations.
5. **Report results** — after execution, update the task state and inform the user.

## Graph Reasoning
Before creating nodes or edges, call `read_knowledge(topic)` to load the correct rules:
- `node_creation_rules` — which task type to use (ATOMIC, FOLLOW_UP, RECURRING, etc.)
- `edge_creation_rules` — which edge type to use (DEPENDS_ON, BLOCKS, PART_OF, etc.)
- `state_machine_rules` — valid state transitions
- `goal_inference_rules` — how to retrieve the graph and when to load tasks vs goals

## Node Retrieval Strategy
- Start at the goal level — fetch active goals first, not all tasks.
- Expand to tasks only when planning or executing against a specific goal.
- A completed goal's tasks are irrelevant — skip unless the user asks.
- Use `list_tasks(goal_id=GOAL-xxx)` to scope to one goal's task subgraph.

Always be concise, warm, and proactive. If you see something the user should know about
(blocked tasks, overdue items, upcoming deadlines), mention it briefly.
"""

# ---------------------------------------------------------------------------
# Knowledge files
# ---------------------------------------------------------------------------

_KNOWLEDGE: dict[str, str] = {
    "node_creation_rules": """\
# Node Creation Rules

Use these rules to decide which task type to create.

## Task Types

| Type | When to create |
|------|---------------|
| ATOMIC | A single discrete step that one person or agent can complete directly. No sub-tasks. |
| COMPOSITE | A complex task with multiple sub-tasks. Create child ATOMICs under it. |
| FOLLOW_UP | Waiting for an external party to respond or act. Has a contact and a follow-up deadline. |
| RECURRING | A repeating pattern (daily standups, weekly reviews). Has a recurrence_pattern. |
| DELEGATED | Explicitly handed off to another person or team. Has a delegated_to field. |
| APPROVAL | Waiting for a formal sign-off or decision from a stakeholder. |
| MILESTONE | A key checkpoint or target date in a project. No work items — just a marker. |
| REVIEW | A task to review output or progress (code review, document review). |
| DECISION | A task where a choice must be made before work can continue. |
| CHECKIN | A scheduled check-in or progress review with another person. |
| RESEARCH | Gathering information or investigating something before deciding. |

## Goal vs Task
- Create a GOAL when the user is expressing a desired outcome ("I want to launch the API").
- Create TASKs to represent the concrete steps needed to reach that goal.
- Wire tasks to goals with PART_OF edges.

## Bottom-Up Goal Inference
If the user describes work without stating an explicit goal, infer the implied goal:
1. What outcome does this work lead to?
2. Create a GOAL with that outcome as the title.
3. Create the user's described work as tasks under that goal.
""",
    "edge_creation_rules": """\
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
""",
    "state_machine_rules": """\
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
""",
    "goal_inference_rules": """\
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
- Before transitioning state: `read_knowledge("state_machine_rules")`
- When discussing priorities: `read_knowledge("scoring_context")`
- When discussing follow-ups: `read_knowledge("follow_up_timing")`
""",
    "scoring_context": """\
# Scoring Context

## 7-Factor Scoring System
Each task is scored 0.0–1.0 on seven weighted factors.
The final score determines rank in the action queue.

| Factor | Default Weight | Description |
|--------|---------------|-------------|
| timeline_urgency (W1) | 0.30 | How close is the deadline? Exponential decay as deadline approaches. |
| dependency_weight (W2) | 0.20 | How many tasks depend on this one? More dependents = higher score. |
| critical_path (W3) | 0.20 | Is this on the critical path? Blocking chain analysis. |
| blocker (W4) | 0.15 | Is this blocking other tasks? Direct blockers score highest. |
| human_override (W5) | 0.05 | Has the user manually flagged this as urgent? |
| resource_risk (W6) | 0.05 | Is the assigned resource overloaded or unavailable? |
| constraint_pressure (W7) | 0.05 | Are there active constraints applying pressure? |

## What Affects Priority
- Tasks with an imminent deadline and many dependents rank highest.
- BLOCKED tasks rank lower (can't act on them) unless the blocker is resolvable.
- FOLLOW_UP tasks escalate in score as their follow-up deadline passes.
- Human overrides (W5) temporarily boost a task's score for one cycle.

## Action Queue
The action queue shows tasks sorted by final_score descending.
Rank 1 = most urgent action the agent should take next.
The top-5 tasks appear in the system prompt graph summary each session.

## User Customisation
Users can adjust W1-W7 weights in their scoring_weights.json to reflect their priorities.
A user who values deadlines over dependencies would raise W1 and lower W2.
""",
    "follow_up_timing": """\
# Follow-Up Timing Rules

## FOLLOW_UP Task Behaviour
A FOLLOW_UP task tracks "waiting for X from Y" situations.
It has a contact, a follow-up deadline, and an urgency escalation schedule.

## Default Escalation by Domain

| Domain | First follow-up | Second follow-up | Escalate |
|--------|----------------|-----------------|---------|
| Business/Contract | 3 days | 7 days | 14 days |
| Legal/Compliance | 5 days | 10 days | 21 days |
| Internal team | 1 day | 3 days | 7 days |
| Vendor/Supplier | 5 days | 10 days | 21 days |
| Personal | 7 days | 14 days | 30 days |

## Urgency Escalation
- Day 0: FOLLOW_UP created, initial message sent.
- First follow-up: Gentle nudge ("Just checking in...").
- Second follow-up: More direct ("Haven't heard back — is there a blocker?").
- Escalate: Involve manager or escalation contact; bump task priority to P1.

## When to Create a FOLLOW_UP Task
- User sends an email, Slack message, or request and is waiting for a response.
- A task was delegated to an external party and no confirmation received.
- A decision was requested from a stakeholder.

## Closing FOLLOW_UP Tasks
- When the contact responds: transition to COMPLETE, spawn an ATOMIC task if action is needed.
- When the contact confirms completion: transition to COMPLETE.
- When the request is no longer needed: transition to CANCELLED.
""",
}

# ---------------------------------------------------------------------------
# Comms agent content
# ---------------------------------------------------------------------------

_COMMS_PROFILE = """\
# Communications Agent (comms)

You are the Communications Agent for GraphClaw. Your job is to read messages from
the user's communication channels (email, Telegram, WhatsApp) and produce concise summaries.

## Your Responsibilities
1. Read new messages from the user's configured MCP integration servers.
2. Identify which messages are relevant to active tasks or goals.
3. Produce a compact summary of each relevant message.
4. If a message requires action, note the required action and the task it relates to.
5. Never reply to messages — only read and summarise.

## Output Format
For each relevant message:
- **From:** [sender]
- **Channel:** [email | telegram | whatsapp]
- **Re:** [subject or context]
- **Summary:** [1-2 sentence summary]
- **Action needed:** [yes/no — if yes, describe the action]
- **Task:** [TSK-xxx if matched to an existing task]

## How to Read Messages
Use the `call_mcp_tool` tool with the relevant MCP server:
- For email: call the user's email MCP server (list servers with `list_mcp_tools`).
- For Telegram: call the user's Telegram MCP server.
- For WhatsApp: call the user's WhatsApp MCP server.

## Trust Reminder
Only call MCP servers with trust_tier GATED or AUTO. Do not call BLOCKED servers.
Read 10 most recent messages per channel. Stop after summarising 20 total messages.
"""

_COMMS_MANIFEST = {
    "agent_id": "comms",
    "name": "Communications Agent",
    "type": "system",
    "description": "Reads messages from email, Telegram, and WhatsApp via MCP integrations",
    "capabilities": ["email_read", "telegram_read", "whatsapp_read", "message_summarise"],
    "invocation": "async",
    "tool_hint": "Delegate to when user asks about messages, replies, or communications from contacts",
}

_COMMS_CONFIG = {
    "max_messages_per_channel": 10,
    "max_total_messages": 20,
    "channels": ["email", "telegram", "whatsapp"],
}


# ---------------------------------------------------------------------------
# Main seeding function
# ---------------------------------------------------------------------------


async def seed_system_content(storage: StorageClient) -> None:
    """Seed all system content into object storage. Idempotent — skips existing objects.

    Parameters
    ----------
    storage:
        The configured StorageClient (MinIO / S3).
    """
    seeded = 0
    skipped = 0

    async def _seed(path: str, content: bytes, content_type: str = "text/plain") -> None:
        nonlocal seeded, skipped
        try:
            exists = await storage.exists(path)
            if exists:
                skipped += 1
                return
            await storage.write(path, content, content_type=content_type)
            seeded += 1
            logger.info("seeding: wrote %s", path)
        except Exception as exc:
            logger.warning("seeding: failed to write %s — %s", path, exc)

    # 1. System prompt header
    await _seed(
        StoragePaths.system_prompt_header(),
        _SYSTEM_HEADER.encode(),
        "text/markdown",
    )

    # 2. Knowledge files
    for topic, content in _KNOWLEDGE.items():
        await _seed(
            StoragePaths.system_knowledge(topic),
            content.encode(),
            "text/markdown",
        )

    # 3. Comms agent
    await _seed(
        StoragePaths.system_agent_profile("comms"),
        _COMMS_PROFILE.encode(),
        "text/markdown",
    )
    await _seed(
        StoragePaths.system_agent_manifest("comms"),
        json.dumps(_COMMS_MANIFEST, indent=2).encode(),
        "application/json",
    )
    await _seed(
        StoragePaths.system_agent_config("comms"),
        json.dumps(_COMMS_CONFIG, indent=2).encode(),
        "application/json",
    )

    logger.info(
        "seeding: complete — seeded=%d skipped=%d",
        seeded,
        skipped,
    )


__all__ = ["seed_system_content"]
