---
name: skill-agent-runtime
description: >
  Skill agent runtime patterns for GraphClaw — SKILL.md parsing, async worker pool,
  heartbeat protocol, status.md pipeline, LLM provider routing via LiteLLM, and
  failure recovery. Use when implementing skill execution, worker management, heartbeat
  monitoring, or status reporting. Triggers on: "skill agent", "SKILL.md", "worker pool",
  "heartbeat", "status.md", "skill execution", "LiteLLM".
---

# Skill Agent Runtime

## SKILL.md Format (PRD Section 30)

```markdown
---
skill_id: research-agent
display_name: Research Agent
version: "1.0"
llm_provider: any          # any | fast | best
llm_model: null             # null = use default for provider class
output_format: markdown
max_context_tokens: 8000
timeout_minutes: 30
---

# Research Agent

## Instructions
<system prompt for the skill agent LLM call>

## Context Variables
- {task.title}, {task.description}
- {user.name}, {user.preferences}
- {goal.title}, {goal.priority}

## Output Schema
- summary: string (required)
- findings: list[string]
- confidence: float (0-1)
- sources: list[string]
```

## Worker Pool Architecture

```python
class SkillWorkerPool:
    max_concurrent: int = 5  # per container
    workers: dict[str, SkillWorker]  # task_id -> worker

    async def submit(self, task_id: str, skill_path: str, context: dict) -> str
    async def cancel(self, task_id: str) -> bool
    async def get_status(self, task_id: str) -> WorkerStatus
```

### Thread States
```
QUEUED → SPAWNED → LOADING → RUNNING → WRITING → COMPLETE | FAILED | CANCELLED
```

### Execution Steps
1. Write initial `status.md` with STATUS: IN_PROGRESS, PROGRESS: 0
2. Load SKILL.md from storage, parse frontmatter + instructions
3. Load task context (task.md, relevant graph data)
4. Resolve LLM config: provider → model → API key (via SecretsClient)
5. Build prompt with context variable injection
6. Call LLM API (via LiteLLM), write progress at 25/50/75%
7. Parse output, write `output.md` + artifacts to storage
8. Write STATUS: COMPLETE (triggers completion signal via broker)

## Heartbeat Protocol

```python
HEARTBEAT_INTERVAL = 300      # 5 minutes
HEARTBEAT_TIMEOUT = 900       # 15 minutes → escalation
MAX_RESPAWN_ATTEMPTS = 3

async def heartbeat_loop(worker: SkillWorker):
    while worker.state in (WorkerState.RUNNING, WorkerState.LOADING):
        await write_status(worker.task_id, progress=worker.progress)
        await asyncio.sleep(HEARTBEAT_INTERVAL)
```

## status.md Format
```yaml
---
task_id: TSK-XX-1234-ATM
agent: research-agent
updated_at: 2026-03-18T10:30:00Z
---
STATUS: IN_PROGRESS
PROGRESS: 50
NOTES: Completed literature review, synthesizing findings
ARTIFACTS: []
```

## LLM Provider Routing (LiteLLM)
```python
# Map skill preference to LiteLLM model string
PROVIDER_MAP = {
    "fast": "anthropic/claude-3-5-haiku-latest",
    "best": "anthropic/claude-sonnet-4-20250514",
    "any":  "anthropic/claude-sonnet-4-20250514",  # default
}
```

## Failure Recovery Decision Tree
1. Heartbeat timeout (15 min) → Check status.md for last state
2. If RUNNING with progress > 0 → Re-spawn from last checkpoint (up to 3x)
3. If LOADING or progress = 0 → Re-spawn fresh (up to 3x)
4. After 3 failed re-spawns → Set STATUS: FAILED, surface in daily briefing
5. LLM rate limit → Exponential backoff (30s, 60s, 120s), then FAILED
6. Context overflow → Truncate context, retry once, then FAILED with error type

---

## Sub-Agent Runtime (Phase 5 Extension)

Sub-agents are long-running autonomous agents delegated tasks by the main `AgentLoop`. They differ from skill workers in scope and lifecycle.

### SubAgentRunner vs SkillWorker

| Aspect | SkillWorker | SubAgentRunner |
|--------|-------------|----------------|
| Trigger | `invoke_skill` tool call | `delegate_to_agent` tool call |
| Lifecycle | Single LLM call with timeout | Multi-step LLM loop (up to 15 iterations) |
| Tools available | None (pure execution) | `invoke_skill`, `call_mcp_tool` |
| Can delegate further | N/A | No (flat delegation, depth = 2) |
| Result delivery | `ResultCollector` polling | `AGENT_UPDATES` broker queue |
| Heartbeat interval | 300s | 60s |
| Timeout behaviour | Mark TIMED_OUT, re-queue | Mark BLOCKED + escalate (no retry) |
| Worker pool | Shared orchestrator pool | Dedicated sub-agent pool |

### SubAgentPool Architecture

```python
class SubAgentRunner:
    state: RunnerState  # IDLE | RUNNING | COMPLETED | FAILED | TIMED_OUT
    agent_id: str
    task_id: str | None
    session_id: str | None
    last_heartbeat: datetime

    async def execute(self, job: AgentJobEvent) -> None:
        # 1. Emit AgentTaskStartedEvent to AGENT_UPDATES
        # 2. Load profile.md + delegation context from MinIO
        # 3. LLM loop: complete() with invoke_skill / call_mcp_tool tools
        # 4. Emit AgentTaskProgressEvent every LLM iteration
        # 5. Emit AgentHeartbeatEvent every HEARTBEAT_INTERVAL seconds
        # 6. On completion: emit AgentTaskCompletedEvent
        # 7. On exception: emit AgentTaskBlockedEvent

class SubAgentPool:
    max_size: int                    # GRAPHCLAW_MAX_CONCURRENT_AGENTS
    runners: list[SubAgentRunner]
    batch_coordinator: BatchCoordinator

    async def start(self) -> None    # launch consumer loop on AGENT_JOBS queue
    async def stop(self) -> None
    def get_runner_statuses(self) -> list[RunnerStatus]
```

### BatchCoordinator (Fan-in)

Tracks completion of parallel dispatch tiers. When all runners in a tier complete, dispatches the next tier. After the final tier, publishes `DELEGATION_COMPLETE` to `TRIGGER_EVENTS` to re-engage the orchestrator.

```python
class BatchCoordinator:
    # batch_id -> (total_count, completed_count, next_tier_task_ids)
    _batches: dict[str, BatchState]

    def register_batch(self, batch_id: str, count: int, next_tier: list[str]) -> None
    def record_completion(self, batch_id: str) -> bool  # returns True if tier done
```

### AgentDispatchPlanner (Topological Sort)

```python
class AgentDispatchPlanner:
    async def plan(self, task_ids: list[str]) -> list[list[str]]:
        # 1. Query GraphQueryEngine for DEPENDS_ON edges among task_ids
        # 2. Build adjacency list for the subgraph
        # 3. Kahn's algorithm topological sort
        # 4. Return ordered tiers: [[independent], [next_tier], ...]
        # Example: tasks A→B→C returns [[C], [B], [A]]
```

### AgentHealthMonitor

```python
AGENT_HEARTBEAT_INTERVAL = 60    # seconds between emits
AGENT_HEARTBEAT_TIMEOUT  = 300   # seconds before BLOCKED

class AgentHealthMonitor:
    async def record_heartbeat(self, agent_id: str, task_id: str) -> None
    async def check_timeouts(self) -> None
        # Runs every 30s; for each stale agent:
        # 1. StateMachine.transition(task_id, BLOCKED)
        # 2. Emit AgentTaskBlockedEvent to AGENT_UPDATES
        # 3. EscalationService.check_and_escalate()
```
