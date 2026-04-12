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
from typing import TYPE_CHECKING, Any

from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import ActionQueueEntry
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore
    from graphclaw.infra.logger import AsyncLogger
    from graphclaw.infra.storage import StorageClient
    from graphclaw.llm.base import LLMClient
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)

# Sentinel agent_id used when no explicit agent_id is configured
_DEFAULT_AGENT_ID = "main"

# System prompt template — persona loaded from profile.md is appended
_SYSTEM_PROMPT_HEADER = """\
You are an AI task orchestration agent for GraphClaw. Your role is to help the user manage \
their tasks, goals, and projects through natural conversation.

You have access to the user's live task graph. You can read tasks, create new tasks or goals, \
update task states, and provide intelligent briefings — all via the tools available to you.

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
    ) -> None:
        self._repo = graph_repo
        self._engine = scoring_engine
        self._sm = state_machine
        self._llm = llm_client
        self._storage = storage_client
        self._agent_id = agent_id
        self._logger = _logger
        # Cache last action queue so system prompt can include current priorities
        self._last_queue: list[ActionQueueEntry] = []
        # Track current session_id for structured logging
        self._current_session_id: str | None = None

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
        parts: list[str] = [_SYSTEM_PROMPT_HEADER]

        # Load agent profile.md from storage if available
        persona = await self._load_agent_profile(user_id)
        if persona:
            parts.append(f"\n## Your Persona\n{persona}")

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
                                "open",
                                "in_progress",
                                "blocked",
                                "complete",
                                "cancelled",
                                "snoozed",
                            ],
                            "description": "New state for the task.",
                        },
                        "reason": {"type": "string", "description": "Reason for the state change."},
                    },
                    "required": ["task_id", "new_state"],
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
            elif name == "get_task_details":
                result = await self._tool_get_task_details(arguments)
            elif name == "check_inbox":
                result = await self._tool_check_inbox(user_id, arguments)
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
                "changed_by": user_id,
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

    async def _tool_get_task_details(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        props = await self._repo.get_node(task_id)
        if not props:
            return {"error": f"Task {task_id} not found"}
        return props

    async def _tool_check_inbox(self, user_id: str, args: dict) -> str:
        """Read recent compact inbox entries from MinIO inbox/recent/ prefix."""
        if self._storage is None:
            return json.dumps({"error": "storage not configured"})
        
        limit = min(int(args.get("limit", 5)), 20)
        from_sender = args.get("from_sender", "").lower().strip()
        channel_filter = args.get("channel", "").lower().strip()
        
        from graphclaw.infra.storage import StoragePaths
        prefix = StoragePaths.agent_inbox_recent_prefix(user_id, self._agent_id)
        
        try:
            keys = await self._storage.list_objects(prefix)  # returns list of object keys
        except Exception:
            return json.dumps({"error": "could not list inbox"})
        
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
            
            results.append({
                "sender": entry.get("sender"),
                "subject": entry.get("subject"),
                "body_summary": entry.get("body_summary"),
                "channel": entry.get("channel"),
                "received_at": entry.get("received_at"),
                "task_id_matched": entry.get("task_id_matched"),
            })
        
        if not results:
            return json.dumps({"message": "No recent inbox messages found.", "count": 0})
        return json.dumps({"messages": results, "count": len(results)})

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
                out[key] = json.loads(val)
                continue
            except (ValueError, TypeError):
                pass
        out[key] = val
    return out


__all__ = ["AgentLoop"]
