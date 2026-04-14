"""graphclaw.agent.loop — AgentLoop: orchestrates the scoring cycle and builds the action queue.

Description
-----------
Provides the ``AgentLoop`` class, which is the primary entry point for the
agent-side of the GraphClaw system.  One call to ``run_cycle()`` fetches all
active tasks, builds a ``ScoringContext`` by querying graph relationships for each
task, scores all tasks via ``ScoringEngine.score_all()``, and returns a ranked
``ActionQueueEntry`` list.  The optional ``generate_briefing()`` method delegates
to the briefing formatter for human-readable output.

``process_chat_message()`` enables full conversational agent interaction: it
loads the agent profile and graph context, builds an LLM conversation with
tool-use support for task/goal mutations, and returns the agent reply.

Design Patterns
---------------
- Facade: ``AgentLoop`` hides the complexity of fetching, context-building, scoring,
  and formatting behind a simple ``run_cycle()`` call.
- Dependency Injection: GraphStore, ScoringEngine, StateMachine, LLMClient, and
  StorageClient are injected at construction time, making the loop fully testable
  with stubs.

Public API
----------
- AgentLoop.run_cycle: Execute one full agent scoring cycle and return the action queue.
- AgentLoop.build_scoring_context: Build a ScoringContext for a given task list.
- AgentLoop.generate_briefing: Generate a human-readable briefing from the action queue.
- AgentLoop.process_chat_message: Handle a conversational user message with LLM + tool-use.

Dependencies
------------
- graphclaw.db.base: GraphStore ABC (TYPE_CHECKING only for type hints).
- graphclaw.state.machine: StateMachine (TYPE_CHECKING only for type hints).
- graphclaw.scoring.engine: ScoringEngine, ScoringContext.
- graphclaw.models.nodes: GoalNode, ResourceNode, TaskNode.
- graphclaw.models.scoring: ActionQueueEntry.
- graphclaw.agent.briefing: format_briefing (imported lazily to avoid circular imports).
- graphclaw.llm.base: LLMClient, LLMMessage, ToolDefinition (TYPE_CHECKING).
- graphclaw.infra.storage: StorageClient, StoragePaths (TYPE_CHECKING).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import ActionQueueEntry
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

if TYPE_CHECKING:
    from graphclaw.agent.dispatch_planner import AgentDispatchPlanner
    from graphclaw.agent.sub_agent_pool import SubAgentPool
    from graphclaw.db.base import GraphStore
    from graphclaw.infra.broker import MessageBroker
    from graphclaw.infra.logger import AsyncLogger
    from graphclaw.infra.storage import StorageClient
    from graphclaw.llm.base import LLMClient
    from graphclaw.mcp.registry import MCPRegistry
    from graphclaw.skills.registry import SkillRegistryService
    from graphclaw.skills.worker import WorkerPool
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)

# Sentinel agent_id used when no explicit agent_id is configured
_DEFAULT_AGENT_ID = "main"

# System prompt template — persona loaded from profile.md is appended
_SYSTEM_PROMPT_HEADER = """\
You are an AI task orchestration agent for GraphClaw. Your role is to help the user manage \
their tasks, goals, and projects through natural conversation — AND to plan and execute work \
using available skills, MCP tools, and agents.

You have access to the user's live task graph. You can read tasks, create new tasks or goals, \
update task states, and provide intelligent briefings — all via the tools available to you.

## Planning & Execution Philosophy
When the user asks you to DO something (not just track it), follow this workflow:
1. **Propose a plan** — call `propose_plan` to decompose the work into subtasks with \
dependencies, assigned skills/agents, and effort estimates. Present it to the user for review.
2. **Wait for approval** — NEVER commit a plan without the user saying yes.
3. **Execute the plan** — call `execute_plan` to create all tasks in the graph.
4. **Delegate actionable tasks** — for each task that can be done by AI:
   - Check `list_available_skills` to find matching skills.
   - Check `list_mcp_tools` to find matching external tools.
   - Use `invoke_skill` for short AI tasks (< 30s).
   - Use `delegate_to_agent` for long-running or complex tasks.
   - Use `call_mcp_tool` for external integrations (GitHub, Calendar, Slack, etc.).
5. **Report results** — after execution, update the task state and inform the user.

If no existing skill or agent can handle a task, use `create_agent` to create a new \
specialised agent, then delegate to it.

When the user asks "how will you do it?" or "share the plan", call `propose_plan` and \
present the structured breakdown with which skills/tools you'll use for each step.

When the user asks you to do something that requires a graph action, USE the appropriate tool. \
Do not say "I will do X" — actually call the tool and report what you did.

Always be concise, warm, and proactive. If you see something the user should know about in \
their task graph (blocked tasks, overdue items, upcoming deadlines), mention it briefly.
"""


class AgentLoop:
    """Orchestrates one scoring cycle of the GraphClaw agent.

    Parameters
    ----------
    graph_repo:
        GraphStore instance for reading nodes and edges.
    scoring_engine:
        ScoringEngine instance used to score tasks.
    state_machine:
        StateMachine instance (available for transition operations if needed
        by future extensions).
    llm_client:
        Optional LLMClient for conversational message processing.  When
        ``None``, ``process_chat_message`` returns a placeholder response.
    storage_client:
        Optional StorageClient for loading agent profile/memory.  When
        ``None``, the system prompt falls back to the built-in header.
    agent_id:
        Logical agent identifier used as the MinIO sub-path for profile
        and memory objects (default ``"main"``).
    skill_registry:
        Optional SkillRegistryService for discovering and loading skills.
    worker_pool:
        Optional WorkerPool for executing skill jobs.
    mcp_registry:
        Optional MCPRegistry for discovering user's MCP servers and tools.
    """

    def __init__(
        self,
        graph_repo: GraphStore,
        scoring_engine: ScoringEngine,
        state_machine: StateMachine,
        llm_client: LLMClient | None = None,
        storage_client: StorageClient | None = None,
        agent_id: str = _DEFAULT_AGENT_ID,
        _logger: AsyncLogger | None = None,
        skill_registry: SkillRegistryService | None = None,
        worker_pool: WorkerPool | None = None,
        mcp_registry: MCPRegistry | None = None,
        broker: MessageBroker | None = None,
        dispatch_planner: AgentDispatchPlanner | None = None,
        sub_agent_pool: SubAgentPool | None = None,
    ) -> None:
        self._repo = graph_repo
        self._engine = scoring_engine
        self._sm = state_machine
        self._llm = llm_client
        self._storage = storage_client
        self._agent_id = agent_id
        self._logger = _logger
        self._skill_registry = skill_registry
        self._worker_pool = worker_pool
        self._mcp_registry = mcp_registry
        self._broker = broker
        self._dispatch_planner = dispatch_planner
        self._sub_agent_pool = sub_agent_pool
        # Cache last action queue so system prompt can include current priorities
        self._last_queue: list[ActionQueueEntry] = []
        # Track current session_id for structured logging
        self._current_session_id: str | None = None
        # Buffer for delegation calls within a single LLM turn (batch dispatch)
        self._turn_delegation_calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_cycle(self) -> list[ActionQueueEntry]:
        """Execute one full agent reasoning cycle.

        Steps
        -----
        1. Fetch all active (non-terminal) TaskNode records from the graph.
        2. Build a ScoringContext by querying relationships for each task.
        3. Score all tasks via ScoringEngine.score_all().
        4. Return the ranked ActionQueueEntry list.

        Returns
        -------
        list[ActionQueueEntry]
            Sorted descending by final_score with rank assigned.
        """
        logger.info("AgentLoop: starting scoring cycle")

        # 1. Fetch active tasks.
        tasks = await self._fetch_active_tasks()
        logger.info("AgentLoop: fetched %d active tasks", len(tasks))

        if not tasks:
            return []

        # 2. Build scoring context.
        context = await self.build_scoring_context(tasks)

        # 3. Score all tasks and return.
        queue = await self._engine.score_all(tasks, context)
        self._last_queue = queue
        logger.info("AgentLoop: scoring cycle complete — %d items in queue", len(queue))

        # Log scoring cycle completion
        if self._logger:
            self._logger.log(
                "INFO",
                "agent.scoring_cycle",
                session_id="",
                user_id="system",
                tasks_scored=len(tasks),
                top_task_id=queue[0].node_id if queue else None,
                queue_depth=len(queue),
            )

        return queue

    async def build_scoring_context(self, tasks: list[TaskNode]) -> ScoringContext:
        """Build a ScoringContext for the given task list.

        Queries the graph to populate:
        - task_goal_priority — parent GoalNode priority per task
        - task_direct_dependents — count of direct dependents per task
        - task_transitive_dependents — count of transitive dependents per task
        - task_blocker_type — blocker edge strength per task
        - task_resource_reliability — assigned resource reliability per task
        - task_resource_load_factor — assigned resource load per task
        - task_resource_risk_signals — resource risk signal per task
        - task_constraints — list of constraint dicts per task

        Falls back to safe defaults if any individual lookup fails.

        Parameters
        ----------
        tasks:
            The task list to build context for.

        Returns
        -------
        ScoringContext
            Populated context ready for ScoringEngine.score_all().
        """
        from graphclaw.models.enums import GoalPriority

        task_goal_priority: dict[str, GoalPriority] = {}
        task_direct_dependents: dict[str, int] = {}
        task_transitive_dependents: dict[str, int] = {}
        task_blocker_type: dict[str, str] = {}
        task_resource_reliability: dict[str, float] = {}
        task_resource_load_factor: dict[str, float] = {}
        task_resource_risk_signals: dict[str, float] = {}
        task_constraints: dict[str, list[dict]] = {}

        for task in tasks:
            tid = task.id

            # --- Goal priority ---
            try:
                goal_edges = await self._repo.get_edges(
                    tid, direction="out", edge_type="APPLIES_TO"
                )
                if not goal_edges:
                    # Also check PART_OF
                    goal_edges = await self._repo.get_edges(
                        tid, direction="out", edge_type="PART_OF"
                    )
                priority = GoalPriority.P3
                for edge in goal_edges:
                    goal_id = edge.get("_end_id")
                    if goal_id:
                        goal_props = await self._repo.get_node(goal_id)
                        if goal_props and goal_props.get("priority"):
                            try:
                                priority = GoalPriority(goal_props["priority"])
                            except ValueError:
                                pass
                            break
                task_goal_priority[tid] = priority
            except Exception as exc:
                logger.debug("build_scoring_context: goal lookup failed for %s: %s", tid, exc)
                task_goal_priority[tid] = GoalPriority.P3

            # --- Dependency counts ---
            try:
                # Direct dependents: tasks that depend directly on this task
                # (T)-[:DEPENDS_ON]->(task) — inbound DEPENDS_ON edges
                direct_edges = await self._repo.get_edges(
                    tid, direction="in", edge_type="DEPENDS_ON"
                )
                direct_count = len(direct_edges)
                task_direct_dependents[tid] = direct_count
                # Use direct count as a proxy for transitive when graph
                # traversal queries are not yet wired; a dedicated query
                # module (db/queries/dependencies.py) handles the full graph.
                task_transitive_dependents[tid] = direct_count
            except Exception as exc:
                logger.debug("build_scoring_context: dep lookup failed for %s: %s", tid, exc)
                task_direct_dependents[tid] = 0
                task_transitive_dependents[tid] = 0

            # --- Blocker type ---
            try:
                blocker_edges = await self._repo.get_edges(tid, direction="out", edge_type="BLOCKS")
                if blocker_edges:
                    strength = blocker_edges[0].get("strength", "HARD")
                    task_blocker_type[tid] = str(strength).upper()
                else:
                    task_blocker_type[tid] = "NONE"
            except Exception as exc:
                logger.debug("build_scoring_context: blocker lookup failed for %s: %s", tid, exc)
                task_blocker_type[tid] = "NONE"

            # --- Resource data ---
            try:
                res_edges = await self._repo.get_edges(
                    tid, direction="out", edge_type="ASSIGNED_TO"
                )
                if res_edges:
                    res_id = res_edges[0].get("_end_id")
                    if res_id:
                        res_props = await self._repo.get_node(res_id)
                        if res_props:
                            reliability_block = res_props.get("reliability", {})
                            capacity_block = res_props.get("capacity", {})
                            task_resource_reliability[tid] = float(
                                reliability_block.get("overall_score", 0.8)
                                if isinstance(reliability_block, dict)
                                else 0.8
                            )
                            task_resource_load_factor[tid] = float(
                                capacity_block.get("load_factor", 0.0)
                                if isinstance(capacity_block, dict)
                                else 0.0
                            )
                            # Risk signals: average of normalised risk levels
                            risk_block = res_props.get("current_risk", {})
                            if isinstance(risk_block, dict):
                                level_map = {"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.0}
                                levels = [
                                    level_map.get(str(risk_block.get(k, "LOW")).upper(), 0.0)
                                    for k in (
                                        "capacity_risk",
                                        "delivery_risk",
                                        "responsiveness_risk",
                                    )
                                ]
                                task_resource_risk_signals[tid] = sum(levels) / len(levels)
                            else:
                                task_resource_risk_signals[tid] = 0.0
            except Exception as exc:
                logger.debug("build_scoring_context: resource lookup failed for %s: %s", tid, exc)

            # --- Constraints ---
            try:
                con_edges = await self._repo.get_edges(tid, direction="in", edge_type="APPLIES_TO")
                constraints: list[dict] = []
                for edge in con_edges:
                    con_id = edge.get("_start_id")
                    if con_id:
                        con_props = await self._repo.get_node(con_id)
                        if con_props:
                            rule = con_props.get("rule", {})
                            constraints.append(
                                {
                                    "threshold": rule.get("threshold")
                                    if isinstance(rule, dict)
                                    else None,
                                    "current_value": rule.get("current_value")
                                    if isinstance(rule, dict)
                                    else None,
                                    "pressure_score": rule.get("pressure_score", 0.0)
                                    if isinstance(rule, dict)
                                    else 0.0,
                                    "hard_limit": rule.get("hard_limit", False)
                                    if isinstance(rule, dict)
                                    else False,
                                }
                            )
                task_constraints[tid] = constraints
            except Exception as exc:
                logger.debug("build_scoring_context: constraint lookup failed for %s: %s", tid, exc)
                task_constraints[tid] = []

        return ScoringContext(
            task_goal_priority=task_goal_priority,
            task_direct_dependents=task_direct_dependents,
            task_transitive_dependents=task_transitive_dependents,
            task_blocker_type=task_blocker_type,
            task_resource_reliability=task_resource_reliability,
            task_resource_load_factor=task_resource_load_factor,
            task_resource_risk_signals=task_resource_risk_signals,
            task_constraints=task_constraints,
            graph_repo=self._repo,
        )

    async def generate_briefing(self, queue: list[ActionQueueEntry], top_n: int = 5) -> str:
        """Generate a human-readable briefing from the action queue.

        Delegates to ``graphclaw.agent.briefing.format_briefing``.

        Parameters
        ----------
        queue:
            Ranked ActionQueueEntry list.
        top_n:
            Number of top entries to include.

        Returns
        -------
        str
            Formatted briefing text.
        """
        from graphclaw.agent.briefing import format_briefing

        return format_briefing(queue, top_n=top_n)

    # ------------------------------------------------------------------
    # Conversational interface
    # ------------------------------------------------------------------

    async def process_chat_message(
        self,
        user_id: str,
        text: str,
        conversation_history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Handle a conversational message from the user.

        Builds a system prompt from the agent profile and current graph context,
        then calls the LLM with the full conversation history and tool definitions
        for graph mutations.  Executes any tool calls the model requests and
        returns the final text response.

        Parameters
        ----------
        user_id:
            The authenticated user's ID.
        text:
            The incoming message text from the user.
        conversation_history:
            Optional prior messages as ``{"role": str, "content": str}`` dicts.
            If ``None``, the LLM sees only the current message.
        session_id:
            Optional session ID for structured logging.

        Returns
        -------
        str
            The agent's text reply (after all tool round-trips complete).
        """
        # Store session_id for tool execution logging
        self._current_session_id = session_id
        if self._llm is None:
            return (
                "I'm not fully initialised yet — the language model is not connected. "
                "Please ensure ANTHROPIC_API_KEY is set and restart the service."
            )

        from graphclaw.llm.base import LLMMessage

        # Build system prompt
        system_prompt = await self._build_system_prompt(user_id)

        # Translate prior history to LLMMessage list
        messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
        for entry in conversation_history or []:
            role = entry.get("role", "user")
            # history may have "agent" role — map to "assistant"
            if role == "agent":
                role = "assistant"
            messages.append(LLMMessage(role=role, content=entry.get("content", "")))
        messages.append(LLMMessage(role="user", content=text))

        # Tool definitions for graph mutations
        tools = self._build_tool_definitions()

        # Agentic loop: call LLM → execute tools → call LLM again until no more tool calls
        for _iteration in range(15):  # safety cap on tool-call rounds
            t0 = time.monotonic()
            response = await self._llm.complete(
                messages,
                model=None,  # use client default
                max_tokens=1024,
                temperature=0.7,
                tools=tools,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            # Log LLM response
            if self._logger and response.usage:
                self._logger.log(
                    "INFO",
                    "agent.message",
                    session_id=session_id or "",
                    user_id=user_id,
                    input_tokens=response.usage.input_tokens if response.usage else 0,
                    output_tokens=response.usage.output_tokens if response.usage else 0,
                    latency_ms=int(elapsed_ms),
                )

            # If the model wants to call tools, execute them and feed results back
            if response.tool_calls:
                # Pre-process: if multiple delegate_to_agent calls exist in this turn,
                # run AgentDispatchPlanner to assign dependency-ordered batch_ids.
                self._turn_delegation_calls = []
                await self._pre_plan_delegation_turn(
                    user_id, session_id or str(uuid.uuid4()), response.tool_calls
                )

                # Append assistant turn with tool calls
                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=list(response.tool_calls),
                    )
                )
                for tc in response.tool_calls:
                    tool_result = await self._execute_tool(user_id, tc.name, tc.arguments)
                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=json.dumps(tool_result),
                            tool_call_id=tc.id,
                        )
                    )
                # Clear buffered delegation calls after the turn completes
                self._turn_delegation_calls = []
                # Continue loop to get final response after tool results
                continue

            # No more tool calls — return the text response
            return response.content or "(no response)"

        # Fallback if loop exhausted
        return "(agent tool-call loop limit reached — please try again)"

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    async def _build_system_prompt(self, user_id: str) -> str:
        """Build a system prompt combining header, agent profile, and graph summary."""
        import datetime as _dt

        today = _dt.date.today().isoformat()
        date_line = f"\nToday's date is {today}. Use this as the reference for all scheduling and deadline reasoning."
        parts: list[str] = [_SYSTEM_PROMPT_HEADER + date_line]

        # Load agent profile.md from storage if available
        persona = await self._load_agent_profile(user_id)
        if persona:
            parts.append(f"\n## Your Persona\n{persona}")

        # Add available skills and MCP context
        exec_ctx = await self._build_execution_context(user_id)
        if exec_ctx:
            parts.append(f"\n{exec_ctx}")

        # Add a brief Task Graph summary (top priorities from last scoring cycle)
        graph_summary = await self._build_graph_summary()
        if graph_summary:
            parts.append(f"\n## Current Task Graph Summary\n{graph_summary}")

        return "\n".join(parts)

    async def _load_agent_profile(self, user_id: str) -> str:
        """Load profile.md from MinIO; return empty string on any failure."""
        if self._storage is None:
            return ""
        try:
            from graphclaw.infra.storage import StoragePaths

            path = StoragePaths.agent_profile(user_id, self._agent_id)
            raw = await self._storage.read(path)
            return raw.decode(errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("AgentLoop: could not load agent profile: %s", exc)
            return ""

    async def _build_graph_summary(self) -> str:
        """Build a brief plain-text task graph snapshot from the last queue."""
        if not self._last_queue:
            # Try a fresh cycle but don't fail if DB is unavailable
            try:
                self._last_queue = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                logger.debug("AgentLoop: graph summary cycle failed: %s", exc)
                return ""

        if not self._last_queue:
            return "No active tasks found."

        # Build a node_id → task index from the last run_cycle result
        task_index: dict[str, Any] = {}
        try:
            tasks = await self._fetch_active_tasks()
            task_index = {t.id: t for t in tasks}
        except Exception:  # noqa: BLE001
            pass

        lines = ["Top priorities:"]
        total_chars = 0
        max_chars = 2500

        for entry in self._last_queue[:5]:
            task = task_index.get(entry.node_id)
            if task is None:
                line = f"- [{entry.rank}] {entry.node_id} | score={entry.final_score:.2f}"
                lines.append(line)
                total_chars += len(line) + 1
                continue

            deadline = ""
            if task.timeline and task.timeline.deadline:
                deadline = f" (due {task.timeline.deadline.date()})"

            main_line = (
                f"- [{entry.rank}] {task.title} | state={task.state}"
                f" | score={entry.final_score:.2f}{deadline}"
            )

            # Add intelligence snippet if available and space permits
            ctx_line = ""
            if task.intelligence and total_chars + len(main_line) + 200 < max_chars:
                snippet = task.intelligence[:180]
                if len(task.intelligence) > 180:
                    snippet += "…"
                ctx_line = f"    [ctx: {snippet}]"

            lines.append(main_line)
            total_chars += len(main_line) + 1

            if ctx_line:
                lines.append(ctx_line)
                total_chars += len(ctx_line) + 1

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool definitions and execution
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tool_definitions() -> list[Any]:
        """Return the ToolDefinition list exposed to the LLM."""
        from graphclaw.llm.base import ToolDefinition

        return [
            # --- Graph read/write tools (original 6) ---
            ToolDefinition(
                name="list_tasks",
                description="List all active tasks in the user's task graph.",
                parameters={
                    "type": "object",
                    "properties": {
                        "state_filter": {
                            "type": "string",
                            "description": "Optional task state to filter by (e.g. 'open', 'in_progress', 'blocked').",
                        }
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="create_goal",
                description="Create a new top-level goal in the task graph.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Goal title."},
                        "description": {"type": "string", "description": "Goal description."},
                        "priority": {
                            "type": "string",
                            "enum": ["P1", "P2", "P3", "P4"],
                            "description": "Goal priority (P1=highest).",
                        },
                        "deadline": {
                            "type": "string",
                            "description": "ISO 8601 deadline date string (e.g. '2026-05-01').",
                        },
                    },
                    "required": ["title"],
                },
            ),
            ToolDefinition(
                name="create_task",
                description=(
                    "Create a new task in the user's task graph. Use task_type='follow_up' "
                    "for follow-up tasks with external contacts."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title."},
                        "description": {"type": "string", "description": "Task description."},
                        "task_type": {
                            "type": "string",
                            "enum": [
                                "atomic",
                                "composite",
                                "follow_up",
                                "research",
                                "approval",
                                "milestone",
                                "review",
                                "recurring",
                                "decision",
                                "checkin",
                                "delegated",
                            ],
                            "description": "Type of task node to create.",
                        },
                        "goal_id": {
                            "type": "string",
                            "description": "Optional goal node ID to link this task to via PART_OF.",
                        },
                        "parent_task_id": {
                            "type": "string",
                            "description": "Optional parent task ID for sub-tasks.",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task IDs this task depends on.",
                        },
                        "assigned_to_contact": {
                            "type": "string",
                            "description": "Email or contact name for follow_up tasks with external parties.",
                        },
                        "deadline": {
                            "type": "string",
                            "description": "ISO 8601 deadline date string.",
                        },
                    },
                    "required": ["title", "task_type"],
                },
            ),
            ToolDefinition(
                name="update_task_state",
                description="Update the state of an existing task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task node ID."},
                        "new_state": {
                            "type": "string",
                            "enum": [
                                "PENDING",
                                "ACTIVE",
                                "IN_PROGRESS",
                                "BLOCKED",
                                "DELAYED",
                                "NEEDS_REVIEW",
                                "COMPLETE",
                                "CANCELLED",
                                "SNOOZED",
                                "INACTIVE_PENDING",
                            ],
                            "description": "New state for the task.",
                        },
                        "reason": {"type": "string", "description": "Reason for the state change."},
                    },
                    "required": ["task_id", "new_state"],
                },
            ),
            ToolDefinition(
                name="update_task",
                description=(
                    "Edit the properties of an existing task — title, description, deadline, "
                    "or assignee. Use this to reschedule deadlines, rename tasks, or reassign "
                    "ownership. To change task *state* use update_task_state instead."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task node ID."},
                        "title": {"type": "string", "description": "New task title."},
                        "description": {"type": "string", "description": "New task description."},
                        "deadline": {
                            "type": "string",
                            "description": "New deadline as an ISO 8601 date string (e.g. '2026-04-23' or '2026-04-23T00:00:00Z').",
                        },
                        "assigned_to": {
                            "type": "string",
                            "description": "User ID or contact reference to assign the task to.",
                        },
                    },
                    "required": ["task_id"],
                },
            ),
            ToolDefinition(
                name="update_goal",
                description=(
                    "Edit the properties of an existing goal — title, description, deadline, "
                    "or priority. Use this to reschedule goal target dates or change priority."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "description": "Goal node ID."},
                        "title": {"type": "string", "description": "New goal title."},
                        "description": {"type": "string", "description": "New goal description."},
                        "deadline": {
                            "type": "string",
                            "description": "New target date as an ISO 8601 date string (e.g. '2026-04-23').",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["P1", "P2", "P3", "P4"],
                            "description": "New priority (P1=highest).",
                        },
                    },
                    "required": ["goal_id"],
                },
            ),
            ToolDefinition(
                name="get_task_details",
                description="Get full details of a specific task by ID.",
                parameters={
                    "type": "object",
                    "properties": {"task_id": {"type": "string", "description": "Task node ID."}},
                    "required": ["task_id"],
                },
            ),
            ToolDefinition(
                name="check_inbox",
                description=(
                    "Check recent inbound messages received from external contacts across all channels "
                    "(email, Telegram, etc.). Returns compact summaries of recent messages. "
                    "Use when the user asks about messages, replies, or communications from other people."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of recent messages to return (default 5, max 20).",
                            "default": 5,
                        },
                        "from_sender": {
                            "type": "string",
                            "description": "Filter by sender email or Telegram username. Leave empty for all senders.",
                            "default": "",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Filter by channel: 'email', 'telegram', 'api', or empty for all channels.",
                            "default": "",
                        },
                    },
                    "required": [],
                },
            ),
            # --- Planning tools (Phase 1) ---
            ToolDefinition(
                name="propose_plan",
                description=(
                    "Decompose a goal or complex task into a structured execution plan with subtasks, "
                    "dependencies, skill/agent assignments, and effort estimates. Returns a proposed plan "
                    "for user review — does NOT commit anything to the graph until execute_plan is called. "
                    "Use this whenever the user asks you to plan, execute, or automate something."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Full description of what needs to be accomplished.",
                        },
                        "constraints": {
                            "type": "string",
                            "description": "Optional constraints (budget, timeline, compliance, technology, etc.).",
                        },
                        "deadline": {
                            "type": "string",
                            "description": "Optional ISO 8601 deadline for the overall goal.",
                        },
                    },
                    "required": ["description"],
                },
            ),
            ToolDefinition(
                name="execute_plan",
                description=(
                    "Execute an approved plan by creating all proposed tasks, goals, and edges "
                    "in the task graph. Call this ONLY after the user has reviewed and approved "
                    "the plan returned by propose_plan."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "plan_id": {
                            "type": "string",
                            "description": "The plan_id returned by propose_plan.",
                        },
                    },
                    "required": ["plan_id"],
                },
            ),
            # --- Skill dispatch tools (Phase 2) ---
            ToolDefinition(
                name="list_available_skills",
                description=(
                    "List skills available to the user (built-in + installed). "
                    "Use to discover what AI capabilities exist before invoking or delegating."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Optional search query to filter skills by name, description, or tags.",
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="invoke_skill",
                description=(
                    "Execute a skill agent to perform a specific task. The skill runs an LLM call "
                    "with a specialised system prompt and returns the output. Use for short AI tasks "
                    "like drafting emails, research summaries, report generation, etc."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to invoke (from list_available_skills).",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Task node ID this skill execution is associated with.",
                        },
                        "input_data": {
                            "type": "object",
                            "description": "Key-value input data for the skill (context, instructions, etc.).",
                        },
                    },
                    "required": ["skill_name", "task_id", "input_data"],
                },
            ),
            # --- MCP tools (Phase 2) ---
            ToolDefinition(
                name="list_mcp_tools",
                description=(
                    "List MCP servers and their available tools for the user. "
                    "MCP tools provide external integrations (GitHub, Calendar, Slack, etc.)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Optional server ID to list tools for a specific server only.",
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="call_mcp_tool",
                description=(
                    "Execute a tool on an MCP server. Respects trust tiers: AUTO tools run immediately, "
                    "GATED tools require user approval, BLOCKED tools are rejected. "
                    "Use for external integrations like creating GitHub issues, scheduling calendar events, etc."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "MCP server ID hosting the tool.",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Name of the MCP tool to call.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments to pass to the MCP tool (matching its input schema).",
                        },
                    },
                    "required": ["server_id", "tool_name", "arguments"],
                },
            ),
            # --- Agent delegation tools (Phase 3) ---
            ToolDefinition(
                name="delegate_to_agent",
                description=(
                    "Delegate a task to an existing skill agent or A2A agent. "
                    "The agent executes the task asynchronously and reports back. "
                    "Updates the task state to IN_PROGRESS."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task node ID to delegate.",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Target agent ID (from the user's agents folder).",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Additional instructions or context for the agent.",
                        },
                    },
                    "required": ["task_id", "agent_id"],
                },
            ),
            ToolDefinition(
                name="create_agent",
                description=(
                    "Create a new specialised agent in the user's agent folder. "
                    "Use when no existing agent or skill can handle the task. "
                    "The agent is created with a profile, config, and assigned skills/MCP servers."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Human-readable name for the agent.",
                        },
                        "purpose": {
                            "type": "string",
                            "description": "What this agent specialises in.",
                        },
                        "skills": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of skill names this agent should use.",
                        },
                        "mcp_servers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of MCP server IDs this agent can access.",
                        },
                    },
                    "required": ["name", "purpose"],
                },
            ),
        ]

    async def _execute_tool(
        self, user_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a tool call and return the result as a dict."""
        t0 = time.monotonic()
        try:
            result: dict[str, Any]
            if name == "list_tasks":
                result = await self._tool_list_tasks(arguments)
            elif name == "create_goal":
                result = await self._tool_create_goal(user_id, arguments)
            elif name == "create_task":
                result = await self._tool_create_task(user_id, arguments)
            elif name == "update_task_state":
                result = await self._tool_update_task_state(user_id, arguments)
            elif name == "update_task":
                result = await self._tool_update_task(user_id, arguments)
            elif name == "update_goal":
                result = await self._tool_update_goal(user_id, arguments)
            elif name == "get_task_details":
                result = await self._tool_get_task_details(arguments)
            elif name == "check_inbox":
                result = await self._tool_check_inbox(user_id, arguments)
            # --- Planning tools ---
            elif name == "propose_plan":
                result = await self._tool_propose_plan(user_id, arguments)
            elif name == "execute_plan":
                result = await self._tool_execute_plan(user_id, arguments)
            # --- Skill tools ---
            elif name == "list_available_skills":
                result = await self._tool_list_available_skills(user_id, arguments)
            elif name == "invoke_skill":
                result = await self._tool_invoke_skill(user_id, arguments)
            # --- MCP tools ---
            elif name == "list_mcp_tools":
                result = await self._tool_list_mcp_tools(user_id, arguments)
            elif name == "call_mcp_tool":
                result = await self._tool_call_mcp_tool(user_id, arguments)
            # --- Agent delegation tools ---
            elif name == "delegate_to_agent":
                result = await self._tool_delegate_to_agent(user_id, arguments)
            elif name == "create_agent":
                result = await self._tool_create_agent(user_id, arguments)
            else:
                result = {"error": f"Unknown tool: {name}"}

            # Log successful tool execution
            if self._logger and "error" not in result:
                self._logger.log(
                    "INFO",
                    "agent.tool_call",
                    session_id=self._current_session_id or "",
                    tool_name=name,
                    user_id=user_id,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )

            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentLoop: tool %s raised %s", name, exc)
            return {"error": str(exc)}

    async def _tool_list_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        tasks = await self._fetch_active_tasks()
        state_filter = args.get("state_filter")
        if state_filter:
            tasks = [t for t in tasks if t.state == state_filter]
        return {
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "state": t.state,
                    "task_type": t.task_type,
                    "deadline": str(t.timeline.deadline)
                    if t.timeline and t.timeline.deadline
                    else None,
                }
                for t in tasks
            ],
            "count": len(tasks),
        }

    async def _tool_create_goal(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        import datetime as _dt

        from graphclaw.models.base import generate_id
        from graphclaw.models.enums import GoalOrigin, GoalPriority, GoalState
        from graphclaw.models.nodes import GoalNode, GoalTimeline

        goal_id = generate_id("GOAL")
        now = _dt.datetime.now(_dt.timezone.utc)
        deadline = None
        if args.get("deadline"):
            try:
                deadline = _dt.datetime.fromisoformat(args["deadline"]).replace(
                    tzinfo=_dt.timezone.utc
                )
            except ValueError:
                pass

        try:
            priority = GoalPriority(args.get("priority", "P3"))
        except ValueError:
            priority = GoalPriority.P3

        goal = GoalNode(
            id=goal_id,
            title=args["title"],
            description=args.get("description", ""),
            owner=user_id,
            priority=priority,
            state=GoalState.ACTIVE,
            origin=GoalOrigin.USER_DEFINED,
            timeline=GoalTimeline(target_date=deadline),
            created_at=now,
            updated_at=now,
        )
        await self._repo.create_node(goal)
        return {"goal_id": goal_id, "title": args["title"], "status": "created"}

    async def _tool_create_task(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.models.base import generate_task_id
        from graphclaw.models.enums import TaskState, TaskType
        from graphclaw.models.nodes import TaskNode

        # Map string task_type to TaskType enum
        _TYPE_MAP: dict[str, TaskType] = {
            "atomic": TaskType.ATOMIC,
            "composite": TaskType.COMPOSITE,
            "follow_up": TaskType.FOLLOWUP,
            "research": TaskType.RESEARCH,
            "approval": TaskType.APPROVAL,
            "milestone": TaskType.MILESTONE,
            "review": TaskType.REVIEW,
            "recurring": TaskType.RECURRING,
            "decision": TaskType.DECISION,
            "checkin": TaskType.CHECKIN,
            "delegated": TaskType.DELEGATED,
        }
        task_type_str = args.get("task_type", "atomic")
        task_type = _TYPE_MAP.get(task_type_str, TaskType.ATOMIC)

        # Agent-generated tasks use "AG" initials
        task_id = generate_task_id("AG", task_type)

        from graphclaw.models.nodes import Timeline

        deadline = None
        if args.get("deadline"):
            import datetime as _dt

            try:
                deadline = _dt.datetime.fromisoformat(args["deadline"]).replace(
                    tzinfo=_dt.timezone.utc
                )
            except ValueError:
                pass

        import datetime as _dt2

        now_task = _dt2.datetime.now(_dt2.timezone.utc)
        task = TaskNode(
            id=task_id,
            task_type=task_type,
            title=args["title"],
            description=args.get("description", ""),
            created_by=user_id,
            owned_by=user_id,
            state=TaskState.PENDING,
            timeline=Timeline(deadline=deadline) if deadline else Timeline(),
            created_at=now_task,
            updated_at=now_task,
        )
        await self._repo.create_node(task)

        # Wire edges
        if args.get("goal_id"):
            try:
                await self._repo.create_edge(task_id, args["goal_id"], "PART_OF", {})
            except Exception as exc:
                logger.warning("AgentLoop: could not wire PART_OF edge: %s", exc)
        if args.get("parent_task_id"):
            try:
                await self._repo.create_edge(task_id, args["parent_task_id"], "PART_OF", {})
            except Exception as exc:
                logger.warning("AgentLoop: could not wire parent PART_OF edge: %s", exc)
        for dep_id in args.get("depends_on") or []:
            try:
                await self._repo.create_edge(task_id, dep_id, "DEPENDS_ON", {})
            except Exception as exc:
                logger.warning("AgentLoop: could not wire DEPENDS_ON edge to %s: %s", dep_id, exc)
        try:
            await self._repo.create_edge(task_id, user_id, "OWNED_BY", {})
        except Exception as exc:
            logger.warning("AgentLoop: could not wire OWNED_BY edge: %s", exc)

        return {
            "task_id": task_id,
            "title": args["title"],
            "task_type": task_type_str,
            "status": "created",
        }

    async def _tool_update_task_state(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        import datetime as _dt

        task_id = args["task_id"]
        new_state = args["new_state"]
        props = await self._repo.get_node(task_id)
        if not props:
            return {"error": f"Task {task_id} not found"}

        current_state = props.get("state", "pending")
        history = list(props.get("state_history", []))
        history.append(
            {
                "from_state": current_state,
                "to_state": new_state,
                "changed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "changed_by": "HUMAN",
                "reason": args.get("reason", ""),
            }
        )
        await self._repo.update_node(task_id, {"state": new_state, "state_history": history})
        return {
            "task_id": task_id,
            "old_state": current_state,
            "new_state": new_state,
            "status": "updated",
        }

    async def _tool_update_task(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        import datetime as _dt
        import json as _json

        task_id = args.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        props = await self._repo.get_node(task_id)
        if not props:
            return {"error": f"Task {task_id} not found"}

        updates: dict[str, Any] = {"updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}

        if "title" in args:
            updates["title"] = args["title"]
        if "description" in args:
            updates["description"] = args["description"]
        if "assigned_to" in args:
            updates["assigned_to"] = args["assigned_to"]

        if "deadline" in args:
            # Read current timeline dict, merge deadline, write back
            raw_timeline = props.get("timeline", {})
            if isinstance(raw_timeline, str):
                try:
                    timeline = _json.loads(raw_timeline)
                except Exception:
                    timeline = {}
            elif isinstance(raw_timeline, dict):
                timeline = dict(raw_timeline)
            else:
                timeline = {}

            try:
                deadline_dt = _dt.datetime.fromisoformat(args["deadline"])
                if deadline_dt.tzinfo is None:
                    deadline_dt = deadline_dt.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                return {
                    "error": f"Invalid deadline format: {args['deadline']!r}. Use ISO 8601 (e.g. '2026-04-23')."
                }

            timeline["deadline"] = deadline_dt.isoformat()
            updates["timeline"] = timeline

        if len(updates) == 1:  # only updated_at — nothing to do
            return {
                "task_id": task_id,
                "status": "no_changes",
                "message": "No fields to update were provided.",
            }

        await self._repo.update_node(task_id, updates)
        changed = [k for k in updates if k != "updated_at"]
        return {"task_id": task_id, "status": "updated", "fields_updated": changed}

    async def _tool_update_goal(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        import datetime as _dt
        import json as _json

        goal_id = args.get("goal_id")
        if not goal_id:
            return {"error": "goal_id is required"}

        props = await self._repo.get_node(goal_id)
        if not props:
            return {"error": f"Goal {goal_id} not found"}

        updates: dict[str, Any] = {"updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}

        if "title" in args:
            updates["title"] = args["title"]
        if "description" in args:
            updates["description"] = args["description"]
        if "priority" in args:
            updates["priority"] = args["priority"]

        if "deadline" in args:
            raw_timeline = props.get("timeline", {})
            if isinstance(raw_timeline, str):
                try:
                    timeline = _json.loads(raw_timeline)
                except Exception:
                    timeline = {}
            elif isinstance(raw_timeline, dict):
                timeline = dict(raw_timeline)
            else:
                timeline = {}

            try:
                deadline_dt = _dt.datetime.fromisoformat(args["deadline"])
                if deadline_dt.tzinfo is None:
                    deadline_dt = deadline_dt.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                return {
                    "error": f"Invalid deadline format: {args['deadline']!r}. Use ISO 8601 (e.g. '2026-04-23')."
                }

            timeline["target_date"] = deadline_dt.isoformat()
            updates["timeline"] = timeline

        if len(updates) == 1:
            return {
                "goal_id": goal_id,
                "status": "no_changes",
                "message": "No fields to update were provided.",
            }

        await self._repo.update_node(goal_id, updates)
        changed = [k for k in updates if k != "updated_at"]
        return {"goal_id": goal_id, "status": "updated", "fields_updated": changed}

    async def _tool_get_task_details(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        props = await self._repo.get_node(task_id)
        if not props:
            return {"error": f"Task {task_id} not found"}
        return props

    async def _tool_check_inbox(self, user_id: str, args: dict) -> str:
        """Read recent compact inbox entries from MinIO inbox/recent/ prefix."""
        if self._storage is None:
            return json.dumps({"messages": [], "note": "Inbox storage not configured."})

        limit = min(int(args.get("limit", 5)), 20)
        from_sender = args.get("from_sender", "").lower().strip()
        channel_filter = args.get("channel", "").lower().strip()

        from graphclaw.infra.storage import StoragePaths

        prefix = StoragePaths.agent_inbox_recent_prefix(user_id, self._agent_id)

        try:
            keys = await self._storage.list_objects(prefix)  # returns list of object keys
        except Exception as exc:
            logger.warning("AgentLoop: check_inbox list failed: %s", exc)
            return json.dumps({"messages": [], "note": "Inbox unavailable."})

        # Sort keys (ISO-prefixed, so alphabetical = chronological)
        keys = sorted(keys, reverse=True)  # newest first

        results = []
        for key in keys:
            if len(results) >= limit:
                break
            try:
                raw = await self._storage.read(key)
                entry = json.loads(raw.decode())
            except Exception:
                continue

            # Apply filters
            if from_sender and from_sender not in entry.get("sender", "").lower():
                continue
            if channel_filter and entry.get("channel", "") != channel_filter:
                continue

            results.append(
                {
                    "sender": entry.get("sender"),
                    "subject": entry.get("subject"),
                    "body_summary": entry.get("body_summary"),
                    "channel": entry.get("channel"),
                    "received_at": entry.get("received_at"),
                    "task_id_matched": entry.get("task_id_matched"),
                }
            )

        if not results:
            return json.dumps({"message": "No recent inbox messages found.", "count": 0})
        return json.dumps({"messages": results, "count": len(results)})

    # ------------------------------------------------------------------
    # Planning tools
    # ------------------------------------------------------------------

    async def _tool_propose_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Use an inner LLM call to decompose a goal into a structured plan."""
        if self._llm is None:
            return {"error": "LLM not configured — cannot generate plans."}

        from graphclaw.llm.base import LLMMessage

        description = args["description"]
        constraints = args.get("constraints", "")
        deadline = args.get("deadline", "")

        # Gather context: available skills and MCP tools
        skills_ctx = await self._gather_skills_summary(user_id)
        mcp_ctx = await self._gather_mcp_summary(user_id)

        planning_prompt = (
            "You are a task decomposition engine. Given a goal, produce a JSON plan.\n\n"
            "Output ONLY valid JSON with this structure:\n"
            '{"goal_title": "...", "goal_description": "...", '
            '"tasks": [{"title": "...", "task_type": "atomic|composite|research|recurring|delegated", '
            '"description": "...", "depends_on_indices": [0, 1], '
            '"assigned_skill": "skill_name or null", '
            '"assigned_mcp_server": "server_id or null", '
            '"assigned_mcp_tool": "tool_name or null", '
            '"effort_estimate": "5m|30m|1h|4h|1d|1w", '
            '"can_be_automated": true}], '
            '"execution_summary": "Brief description of the approach"}\n\n'
            "Rules:\n"
            "- depends_on_indices references other tasks by their 0-based index in the array\n"
            "- Only assign skills/MCP tools that exist in the available list below\n"
            "- Mark can_be_automated=true if a skill or MCP tool can handle it\n"
            "- For recurring tasks, include the schedule in the description\n\n"
        )
        if skills_ctx:
            planning_prompt += f"Available skills:\n{skills_ctx}\n\n"
        if mcp_ctx:
            planning_prompt += f"Available MCP tools:\n{mcp_ctx}\n\n"
        if constraints:
            planning_prompt += f"Constraints: {constraints}\n\n"
        if deadline:
            planning_prompt += f"Deadline: {deadline}\n\n"

        messages = [
            LLMMessage(role="system", content=planning_prompt),
            LLMMessage(role="user", content=description),
        ]

        response = await self._llm.complete(messages, model=None, max_tokens=2048, temperature=0.3)
        raw_output = response.content or ""

        # Parse the LLM JSON output
        try:
            # Strip markdown code fences if present
            cleaned = raw_output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            plan_data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            # Return raw output if not valid JSON so the agent can still present it
            plan_data = {
                "goal_title": description[:80],
                "goal_description": description,
                "tasks": [],
                "execution_summary": raw_output,
                "parse_warning": "Could not parse structured plan — showing raw decomposition.",
            }

        # Generate plan_id and store for later execution
        plan_id = f"PLAN-{uuid.uuid4().hex[:12]}"
        plan_data["plan_id"] = plan_id
        plan_data["user_id"] = user_id
        plan_data["status"] = "proposed"
        if deadline:
            plan_data["deadline"] = deadline

        # Persist to storage if available
        if self._storage:
            from graphclaw.infra.storage import StoragePaths

            plan_path = f"{StoragePaths.agent_root(user_id, self._agent_id)}state/pending_plans/{plan_id}.json"
            try:
                await self._storage.write(plan_path, json.dumps(plan_data).encode())
            except Exception as exc:
                logger.debug("AgentLoop: could not persist plan to storage: %s", exc)

        # Also cache in memory for execute_plan to find
        if not hasattr(self, "_pending_plans"):
            self._pending_plans: dict[str, dict] = {}
        self._pending_plans[plan_id] = plan_data

        return {
            "plan_id": plan_id,
            "goal_title": plan_data.get("goal_title", ""),
            "tasks": plan_data.get("tasks", []),
            "execution_summary": plan_data.get("execution_summary", ""),
            "task_count": len(plan_data.get("tasks", [])),
            "status": "proposed — awaiting user approval",
        }

    async def _tool_execute_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Create all tasks from an approved plan in the graph."""
        plan_id = args["plan_id"]

        # Load plan from memory cache or storage
        plan_data = getattr(self, "_pending_plans", {}).get(plan_id)
        if not plan_data and self._storage:
            from graphclaw.infra.storage import StoragePaths

            plan_path = f"{StoragePaths.agent_root(user_id, self._agent_id)}state/pending_plans/{plan_id}.json"
            try:
                raw = await self._storage.read(plan_path)
                plan_data = json.loads(raw.decode())
            except Exception:
                pass

        if not plan_data:
            return {"error": f"Plan {plan_id} not found. Call propose_plan first."}
        if plan_data.get("status") == "executed":
            return {"error": f"Plan {plan_id} has already been executed."}

        # Create the goal first
        goal_result = await self._tool_create_goal(
            user_id,
            {
                "title": plan_data.get("goal_title", "Untitled Goal"),
                "description": plan_data.get("goal_description", ""),
                "deadline": plan_data.get("deadline", ""),
            },
        )
        goal_id = goal_result.get("goal_id")

        # Create tasks and track created IDs for dependency wiring
        tasks = plan_data.get("tasks", [])
        created_tasks: list[dict[str, Any]] = []
        index_to_task_id: dict[int, str] = {}

        for idx, task_spec in enumerate(tasks):
            # Resolve depends_on from indices to task IDs
            depends_on_ids = []
            for dep_idx in task_spec.get("depends_on_indices", []):
                dep_task_id = index_to_task_id.get(dep_idx)
                if dep_task_id:
                    depends_on_ids.append(dep_task_id)

            task_result = await self._tool_create_task(
                user_id,
                {
                    "title": task_spec.get("title", f"Task {idx + 1}"),
                    "description": task_spec.get("description", ""),
                    "task_type": task_spec.get("task_type", "atomic"),
                    "goal_id": goal_id,
                    "depends_on": depends_on_ids,
                },
            )
            task_id = task_result.get("task_id", "")
            index_to_task_id[idx] = task_id
            created_tasks.append(
                {
                    "task_id": task_id,
                    "title": task_spec.get("title", ""),
                    "task_type": task_spec.get("task_type", "atomic"),
                    "can_be_automated": task_spec.get("can_be_automated", False),
                    "assigned_skill": task_spec.get("assigned_skill"),
                    "assigned_mcp_server": task_spec.get("assigned_mcp_server"),
                    "assigned_mcp_tool": task_spec.get("assigned_mcp_tool"),
                }
            )

        # Mark plan as executed
        plan_data["status"] = "executed"
        if hasattr(self, "_pending_plans"):
            self._pending_plans[plan_id] = plan_data
        if self._storage:
            from graphclaw.infra.storage import StoragePaths

            plan_path = f"{StoragePaths.agent_root(user_id, self._agent_id)}state/pending_plans/{plan_id}.json"
            try:
                await self._storage.write(plan_path, json.dumps(plan_data).encode())
            except Exception:
                pass

        return {
            "plan_id": plan_id,
            "goal_id": goal_id,
            "created_tasks": created_tasks,
            "total_created": len(created_tasks),
            "status": "executed",
        }

    # ------------------------------------------------------------------
    # Skill dispatch tools
    # ------------------------------------------------------------------

    async def _tool_list_available_skills(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """List skills available to the user."""
        if self._skill_registry is None:
            return {"skills": [], "count": 0, "note": "Skill registry not configured."}

        query = args.get("query", "")
        try:
            if query:
                results = await self._skill_registry.search(user_id, query=query)
            else:
                results = await self._skill_registry.list_installed(user_id)
            skills = [
                {
                    "skill_id": getattr(s, "skill_id", getattr(s, "name", "")),
                    "name": getattr(s, "name", ""),
                    "description": getattr(s, "description", ""),
                    "tags": getattr(s, "tags", []),
                    "version": getattr(s, "version", "1.0.0"),
                }
                for s in results
            ]
            return {"skills": skills, "count": len(skills)}
        except Exception as exc:
            logger.warning("AgentLoop: list_available_skills failed: %s", exc)
            # Return an empty list rather than an error string so Betty does not
            # surface infrastructure details (missing credentials, unreachable
            # storage) to the user.
            return {"skills": [], "count": 0, "note": "Skill registry unavailable."}

    async def _tool_invoke_skill(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a skill via the worker pool."""
        if self._worker_pool is None or self._skill_registry is None:
            return {"error": "Skill execution not configured (worker pool or registry missing)."}

        skill_name = args["skill_name"]
        task_id = args["task_id"]
        input_data = args.get("input_data", {})

        # Load the skill definition
        try:
            skill_def = await self._skill_registry.get_skill_definition(user_id, skill_name)
        except Exception as exc:
            return {"error": f"Could not load skill '{skill_name}': {exc}"}

        if skill_def is None:
            return {"error": f"Skill '{skill_name}' not found."}

        # Create and submit a SkillJob
        from graphclaw.models.base import utcnow
        from graphclaw.skills.models import SkillJob

        job = SkillJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            skill_name=skill_name,
            task_id=task_id,
            session_id=self._current_session_id or "",
            input_data=input_data,
            priority=5,
            created_at=utcnow(),
            timeout_seconds=skill_def.timeout_seconds,
        )

        # Get an idle worker and execute directly (synchronous for short skills)
        worker = self._worker_pool.get_idle_worker()
        if worker is None:
            # Submit to queue if no idle worker
            await self._worker_pool.submit(job)
            return {
                "status": "queued",
                "job_id": job.job_id,
                "message": f"All workers busy — job queued for skill '{skill_name}'.",
            }

        result = await worker.execute(job, skill_def)

        # Update task intelligence with skill output
        if result.status.value == "COMPLETED" and result.output:
            try:
                await self._repo.update_node(
                    task_id,
                    {
                        "intelligence": result.output[:2000],
                        "state": "IN_PROGRESS",
                    },
                )
            except Exception as exc:
                logger.debug("AgentLoop: could not update task with skill result: %s", exc)

        return {
            "status": result.status.value,
            "job_id": result.job_id,
            "output": result.output[:3000] if result.output else "",
            "error": result.error or "",
            "tokens_used": result.tokens_used,
            "cost_usd": result.cost_usd,
        }

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    async def _tool_list_mcp_tools(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """List MCP servers and their tools for the user."""
        if self._mcp_registry is None:
            return {"servers": [], "count": 0, "note": "MCP registry not configured."}

        try:
            servers = await self._mcp_registry.list_for_user(user_id, enabled_only=True)
        except Exception as exc:
            logger.warning("AgentLoop: list_mcp_tools failed: %s", exc)
            return {"servers": [], "count": 0, "note": "MCP registry unavailable."}

        server_filter = args.get("server_id")
        if server_filter:
            servers = [s for s in servers if s.id == server_filter]

        results = []
        for server in servers:
            results.append(
                {
                    "server_id": server.id,
                    "name": server.name,
                    "trust_tier": server.trust_tier.value
                    if hasattr(server.trust_tier, "value")
                    else str(server.trust_tier),
                    "transport": server.transport.value
                    if hasattr(server.transport, "value")
                    else str(server.transport),
                    "description": getattr(server, "description", ""),
                }
            )

        return {"servers": results, "count": len(results)}

    async def _tool_call_mcp_tool(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool on an MCP server."""
        if self._mcp_registry is None:
            return {"error": "MCP registry not configured."}

        server_id = args["server_id"]
        tool_name = args["tool_name"]
        arguments = args.get("arguments", {})

        # Look up the server config from storage
        try:
            server = await self._mcp_registry.get(user_id, server_id)
        except Exception as exc:
            return {"error": f"Could not look up MCP server '{server_id}': {exc}"}

        if server is None:
            return {"error": f"MCP server '{server_id}' not found."}

        # Instantiate client, connect, call, disconnect
        from graphclaw.mcp.client import MCPClient

        client = MCPClient()
        success = False
        latency_ms = 0
        try:
            await client.connect(server)
            result = await client.call_tool(
                tool_name=tool_name,
                arguments=arguments,
                trust_tier=server.trust_tier,
                user_id=user_id,
                server_id=server_id,
            )
            success = result.success
            latency_ms = result.latency_ms
            return {
                "success": result.success,
                "content": result.content[:3000] if result.content else "",
                "error": result.error_message or "",
                "latency_ms": result.latency_ms,
            }
        except ImportError:
            return {"error": "MCP SDK not installed. Install with: pip install mcp>=1.0.0"}
        except Exception as exc:
            return {"error": f"MCP tool call failed: {exc}"}
        finally:
            await client.disconnect()
            # Audit log every MCP tool call — success or failure
            if self._logger:
                self._logger.log(
                    "INFO",
                    "mcp.tool_call",
                    session_id=self._current_session_id or "",
                    user_id=user_id,
                    server_id=server_id,
                    server_name=server.name,
                    tool_name=tool_name,
                    success=success,
                    latency_ms=latency_ms,
                )
            # Stamp last_used_at on the server config (best-effort)
            try:
                await self._mcp_registry.update_last_used(user_id, server_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Agent delegation tools
    # ------------------------------------------------------------------

    async def _pre_plan_delegation_turn(
        self,
        user_id: str,
        session_id: str,
        tool_calls: list[Any],
    ) -> None:
        """Pre-compute dispatch tiers for multiple delegate_to_agent calls in one LLM turn.

        If two or more ``delegate_to_agent`` tool calls appear in the same turn and
        ``dispatch_planner`` + ``sub_agent_pool`` are configured, this method:
        1. Builds ``AgentJobEvent`` stubs for each delegation call.
        2. Runs ``AgentDispatchPlanner.plan()`` to obtain dependency-ordered tiers.
        3. Calls ``sub_agent_pool.register_dispatch_plan()`` so ``BatchCoordinator``
           knows which tier to dispatch next.
        4. Stores the computed ``batch_id`` per ``task_id`` in
           ``self._turn_delegation_calls`` so ``_tool_delegate_to_agent()`` picks
           them up when it publishes jobs to ``AGENT_JOBS``.

        If fewer than 2 delegation calls exist, or planner/pool are not configured,
        this method is a no-op — jobs default to a single tier (all parallel).
        """
        delegation_calls = [tc for tc in tool_calls if tc.name == "delegate_to_agent"]
        if not delegation_calls:
            return

        if self._dispatch_planner is None or self._sub_agent_pool is None:
            # No planner — all jobs get the same flat batch_id (parallel)
            flat_batch_id = f"batch-{session_id[:8]}-t0"
            self._turn_delegation_calls = [
                {"task_id": tc.arguments.get("task_id", ""), "batch_id": flat_batch_id}
                for tc in delegation_calls
            ]
            return

        # Build job stubs for the planner
        from graphclaw.agent.sub_agent_runner import AgentJobEvent

        job_stubs = [
            AgentJobEvent(
                agent_id=tc.arguments.get("agent_id", ""),
                task_id=tc.arguments.get("task_id", ""),
                session_id=session_id,
                parent_task_id=None,
                batch_id="",
                instructions=tc.arguments.get("instructions", ""),
            )
            for tc in delegation_calls
        ]

        tiers = await self._dispatch_planner.plan(job_stubs, session_id)
        self._sub_agent_pool.register_dispatch_plan(tiers, session_id)

        # Build task_id → batch_id lookup from the planned tiers
        batch_lookup: dict[str, str] = {}
        for tier in tiers:
            for job in tier:
                batch_lookup[job.task_id] = job.batch_id

        self._turn_delegation_calls = [
            {
                "task_id": tc.arguments.get("task_id", ""),
                "batch_id": batch_lookup.get(
                    tc.arguments.get("task_id", ""), f"batch-{session_id[:8]}-t0"
                ),
            }
            for tc in delegation_calls
        ]
        logger.info(
            "AgentLoop: dispatch plan computed — %d tiers for %d jobs (session=%s)",
            len(tiers),
            len(job_stubs),
            session_id,
        )

    async def _tool_delegate_to_agent(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Delegate a task to an existing agent."""
        task_id = args["task_id"]
        agent_id = args["agent_id"]
        instructions = args.get("instructions", "")

        # Verify the task exists
        task_props = await self._repo.get_node(task_id)
        if not task_props:
            return {"error": f"Task {task_id} not found."}

        # Verify the agent exists in storage
        if self._storage:
            from graphclaw.infra.storage import StoragePaths

            agent_profile_path = StoragePaths.agent_profile(user_id, agent_id)
            try:
                exists = await self._storage.exists(agent_profile_path)
                if not exists:
                    return {"error": f"Agent '{agent_id}' not found in user's agent folder."}
            except Exception:
                pass  # Proceed even if check fails

        # Update task state to IN_PROGRESS and assign to agent
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        history = list(task_props.get("state_history", []))
        history.append(
            {
                "from_state": task_props.get("state", "PENDING"),
                "to_state": "IN_PROGRESS",
                "changed_at": now.isoformat(),
                "changed_by": "AGENT",
                "reason": f"Delegated to agent '{agent_id}'"
                + (f": {instructions}" if instructions else ""),
            }
        )
        await self._repo.update_node(
            task_id,
            {
                "state": "IN_PROGRESS",
                "assigned_to": agent_id,
                "state_history": history,
            },
        )

        # Resolve batch_id from pre-planned dispatch tiers (if available)
        batch_id = next(
            (
                entry["batch_id"]
                for entry in self._turn_delegation_calls
                if entry["task_id"] == task_id
            ),
            f"batch-{(self._current_session_id or uuid.uuid4().hex)[:8]}-t0",
        )

        # Write delegation context to agent's working memory (includes session propagation)
        session_id = self._current_session_id or ""
        if self._storage:
            from graphclaw.infra.storage import StoragePaths

            context_path = StoragePaths.agent_memory_working(user_id, agent_id)
            delegation_ctx = (
                f"# Delegation: {task_id}\n\n"
                f"- **Task:** {task_props.get('title', 'Unknown')}\n"
                f"- **Description:** {task_props.get('description', '')}\n"
                f"- **Instructions:** {instructions}\n"
                f"- **Delegated at:** {now.isoformat()}\n"
                f"- **Delegated by:** orchestrator ({self._agent_id})\n"
                f"- **Session:** {session_id}\n"
                f"- **Batch:** {batch_id}\n"
            )
            try:
                await self._storage.write(context_path, delegation_ctx.encode())
            except Exception as exc:
                logger.debug("AgentLoop: could not write delegation context: %s", exc)

        # Publish AgentJobEvent to AGENT_JOBS so SubAgentPool picks it up
        if self._broker is not None:
            from graphclaw.agent.sub_agent_runner import AgentJobEvent
            from graphclaw.infra.broker import AGENT_JOBS

            job = AgentJobEvent(
                agent_id=agent_id,
                task_id=task_id,
                session_id=session_id,
                parent_task_id=task_props.get("parent_task_id"),
                batch_id=batch_id,
                instructions=instructions,
            )
            try:
                await self._broker.publish(AGENT_JOBS, job.model_dump_json())
                logger.info(
                    "AgentLoop: published AgentJobEvent agent=%s task=%s batch=%s",
                    agent_id,
                    task_id,
                    batch_id,
                )
            except Exception as exc:
                logger.warning("AgentLoop: failed to publish AgentJobEvent: %s", exc)
        else:
            logger.warning(
                "AgentLoop: broker not configured — delegation to agent '%s' is fire-and-forget",
                agent_id,
            )

        return {
            "status": "delegated",
            "task_id": task_id,
            "agent_id": agent_id,
            "task_state": "IN_PROGRESS",
            "batch_id": batch_id,
            "message": f"Task delegated to agent '{agent_id}' (batch: {batch_id}).",
        }

    async def _tool_create_agent(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new agent in the user's agent folder."""
        if self._storage is None:
            return {"error": "Storage not configured — cannot create agents."}

        from graphclaw.infra.storage import StoragePaths

        name = args["name"]
        purpose = args["purpose"]
        skills = args.get("skills", [])
        mcp_servers = args.get("mcp_servers", [])

        # Generate agent_id from name (lowercase, hyphenated)
        agent_id = name.lower().replace(" ", "-").replace("_", "-")[:40]
        # Ensure uniqueness with short uuid suffix
        agent_id = f"{agent_id}-{uuid.uuid4().hex[:6]}"

        # Create profile.md
        profile_content = (
            f"# Agent Profile: {name}\n\n"
            f"## Identity\n"
            f"- **Name:** {name}\n"
            f"- **Agent ID:** {agent_id}\n"
            f"- **Owner:** {user_id}\n"
            f"- **Purpose:** {purpose}\n\n"
            f"## Persona & Style\n"
            f"- Focused, task-oriented, and efficient\n"
            f"- Reports progress proactively\n"
            f"- Escalates blockers immediately\n\n"
            f"## Assigned Skills\n"
        )
        for skill in skills:
            profile_content += f"- {skill}\n"
        if not skills:
            profile_content += "- (none assigned yet)\n"

        profile_content += "\n## MCP Servers\n"
        for srv in mcp_servers:
            profile_content += f"- {srv}\n"
        if not mcp_servers:
            profile_content += "- (none assigned yet)\n"

        # Create config.json
        config = {
            "agent_id": agent_id,
            "name": name,
            "purpose": purpose,
            "skills": skills,
            "mcp_servers": mcp_servers,
            "llm_provider": "litellm",
            "llm_model": "claude-sonnet-4-20250514",
            "heartbeat_interval_seconds": 60,
            "created_by": self._agent_id,
        }

        # Write files
        profile_path = StoragePaths.agent_profile(user_id, agent_id)
        config_path = StoragePaths.agent_config(user_id, agent_id)
        context_path = StoragePaths.agent_memory_working(user_id, agent_id)

        try:
            await self._storage.write(profile_path, profile_content.encode())
            await self._storage.write(config_path, json.dumps(config, indent=2).encode())
            await self._storage.write(
                context_path, b"# Working Context\n\nAgent initialised. Awaiting first task.\n"
            )
        except Exception as exc:
            return {"error": f"Failed to create agent files: {exc}"}

        return {
            "agent_id": agent_id,
            "name": name,
            "purpose": purpose,
            "skills": skills,
            "mcp_servers": mcp_servers,
            "status": "created",
            "profile_path": profile_path,
        }

    # ------------------------------------------------------------------
    # Context gathering helpers (for planning)
    # ------------------------------------------------------------------

    async def _gather_skills_summary(self, user_id: str) -> str:
        """Build a text summary of available skills for the planning prompt."""
        if self._skill_registry is None:
            return ""
        try:
            installed = await self._skill_registry.list_installed(user_id)
            if not installed:
                return ""
            lines = []
            for s in installed[:20]:
                name = getattr(s, "name", "")
                desc = getattr(s, "description", "")
                lines.append(f"- {name}: {desc}")
            return "\n".join(lines)
        except Exception:
            return ""

    async def _gather_mcp_summary(self, user_id: str) -> str:
        """Build a text summary of available MCP servers/tools for the planning prompt."""
        if self._mcp_registry is None:
            return ""
        try:
            servers = await self._mcp_registry.list_for_user(user_id, enabled_only=True)
            if not servers:
                return ""
            lines = []
            for srv in servers[:10]:
                tier = (
                    srv.trust_tier.value
                    if hasattr(srv.trust_tier, "value")
                    else str(srv.trust_tier)
                )
                lines.append(f"- {srv.name} (ID: {srv.id}, trust: {tier})")
            return "\n".join(lines)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # System prompt enrichment with execution context
    # ------------------------------------------------------------------

    async def _build_execution_context(self, user_id: str) -> str:
        """Build a brief summary of available skills and MCP for the system prompt."""
        parts: list[str] = []

        skills_summary = await self._gather_skills_summary(user_id)
        if skills_summary:
            parts.append(f"## Available Skills\n{skills_summary}")

        mcp_summary = await self._gather_mcp_summary(user_id)
        if mcp_summary:
            parts.append(f"## Available MCP Servers\n{mcp_summary}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_active_tasks(self) -> list[TaskNode]:
        """Retrieve all non-terminal TaskNode records from the graph."""
        from graphclaw.models.enums import TaskState

        _TERMINAL = {
            TaskState.COMPLETE.value,
            TaskState.CANCELLED.value,
            TaskState.SNOOZED.value,
        }

        try:
            raw_nodes = await self._repo.list_nodes("TaskNode")
        except Exception as exc:
            logger.warning("AgentLoop: failed to list TaskNode vertices: %s", exc)
            return []

        tasks: list[TaskNode] = []
        for props in raw_nodes:
            if props.get("state") in _TERMINAL:
                continue
            try:
                task = TaskNode.model_validate(_deserialise_graph_props(props))
                tasks.append(task)
            except Exception as exc:
                logger.warning(
                    "AgentLoop: could not parse TaskNode %s: %s",
                    props.get("id", "?"),
                    exc,
                )
        return tasks


def _deserialise_graph_props(props: dict) -> dict:
    """Parse any JSON-string values in an AGE property dict back to Python objects.

    The AGE backend stores nested dicts (e.g. ``timeline``, ``scoring``) as
    JSON strings (because AGE Cypher doesn't natively support nested maps).
    This helper converts those strings back to dicts so that Pydantic's
    ``model_validate`` can parse them correctly.
    """
    out: dict = {}
    for key, val in props.items():
        if isinstance(val, str) and val and val[0] in ("{", "["):
            try:
                parsed = json.loads(val)
                # If the JSON was a list, also decode any JSON-string items within it
                if isinstance(parsed, list):
                    parsed = [
                        json.loads(item)
                        if isinstance(item, str) and item and item[0] in ("{", "[")
                        else item
                        for item in parsed
                    ]
                out[key] = parsed
                continue
            except (ValueError, TypeError):
                pass
        elif isinstance(val, list):
            # AGE returns Cypher lists natively; individual dict items are
            # stored as JSON strings (e.g. state_history entries).  Decode them.
            decoded = []
            for item in val:
                if isinstance(item, str) and item and item[0] in ("{", "["):
                    try:
                        decoded.append(json.loads(item))
                        continue
                    except (ValueError, TypeError):
                        pass
                decoded.append(item)
            out[key] = decoded
            continue
        out[key] = val
    return out


__all__ = ["AgentLoop"]
