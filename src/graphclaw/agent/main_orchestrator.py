"""graphclaw.agent.main_orchestrator — MainOrchestrator core orchestration runtime.

Description
-----------
Provides the ``MainOrchestrator`` class, which is the primary entry point for the
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
- Facade: ``MainOrchestrator`` hides the complexity of fetching, context-building, scoring,
  and formatting behind a simple ``run_cycle()`` call.
- Dependency Injection: GraphStore, ScoringEngine, StateMachine, LLMClient, and
  StorageClient are injected at construction time, making the loop fully testable
  with stubs.

Public API
----------
- MainOrchestrator.run_cycle: Execute one full agent scoring cycle and return the action queue.
- MainOrchestrator.build_scoring_context: Build a ScoringContext for a given task list.
- MainOrchestrator.generate_briefing: Generate a human-readable briefing from the action queue.
- MainOrchestrator.process_chat_message: Handle a conversational user message with LLM + tool-use.

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

Backward Compatibility
----------------------
The class alias ``AgentLoop`` remains available from ``graphclaw.agent`` during
the migration to ``MainOrchestrator`` naming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from graphclaw.agent.catalog import AgentCatalog
from graphclaw.agent.context import ContextManager
from graphclaw.agent.knowledge import KnowledgeBase
from graphclaw.agent.tool_registry import ToolSetRegistry
from graphclaw.infra.logging.events import AgentToolCallEvent
from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import ActionQueueEntry
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from graphclaw.agent.dispatch_planner import AgentDispatchPlanner
    from graphclaw.agent.run_events import AgentRunEvent
    from graphclaw.agent.sub_agent_pool import SubAgentPool
    from graphclaw.db.base import GraphStore
    from graphclaw.infra.broker import MessageBroker
    from graphclaw.infra.storage import StorageClient
    from graphclaw.infra.user_events import UserEventPublisher
    from graphclaw.llm.base import LLMClient
    from graphclaw.mcp.registry import MCPRegistry
    from graphclaw.skills.registry import SkillRegistryService
    from graphclaw.skills.worker import WorkerPool
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)

# Sentinel agent_id used when no explicit agent_id is configured
_DEFAULT_AGENT_ID = "main"
_PLAN_STATUS_DRAFT = "DRAFT"
_PLAN_STATUS_APPROVED = "APPROVED"
_PLAN_STATUS_EXECUTED = "EXECUTED"
_GOAL_INFERENCE_STATUS_DRAFT = "DRAFT"
_GOAL_INFERENCE_STATUS_APPROVED = "APPROVED"

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


class MainOrchestrator:
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
    redis_client:
        Optional async Redis client.  When provided, user agent profiles are
        cached at ``graphclaw:profile:{user_id}`` with a 15-minute TTL and the
        ``AgentCatalog`` uses Redis for user manifest caching.
    """

    # TTL constants for in-process caches
    _SYSTEM_HEADER_TTL: float = 3600.0  # 1 hour
    _USER_PROFILE_REDIS_TTL: int = 900  # 15 minutes
    _USER_PROFILE_KEY_PREFIX = "graphclaw:profile:"

    def __init__(
        self,
        graph_repo: GraphStore,
        scoring_engine: ScoringEngine,
        state_machine: StateMachine,
        llm_client: LLMClient | None = None,
        storage_client: StorageClient | None = None,
        agent_id: str = _DEFAULT_AGENT_ID,
        skill_registry: SkillRegistryService | None = None,
        worker_pool: WorkerPool | None = None,
        mcp_registry: MCPRegistry | None = None,
        broker: MessageBroker | None = None,
        dispatch_planner: AgentDispatchPlanner | None = None,
        sub_agent_pool: SubAgentPool | None = None,
        event_publisher: UserEventPublisher | None = None,
        redis_client: Any | None = None,
        admin_repo: GraphStore | None = None,
    ) -> None:
        self._repo = graph_repo
        # Wave 0 (FR-DEL-002): admin_principal store for archive operations.
        # Falls back to self._repo for backwards-compat tests; real deployments
        # must pass admin_repo so lifecycle-field writes succeed.
        self._admin_repo: GraphStore = admin_repo if admin_repo is not None else graph_repo
        self._engine = scoring_engine
        self._sm = state_machine
        self._llm = llm_client
        self._storage = storage_client
        self._agent_id = agent_id
        self._skill_registry = skill_registry
        self._worker_pool = worker_pool
        self._mcp_registry = mcp_registry
        self._broker = broker
        self._dispatch_planner = dispatch_planner
        self._sub_agent_pool = sub_agent_pool
        self._event_publisher: UserEventPublisher | None = event_publisher
        self._redis = redis_client
        # Cache last action queue so system prompt can include current priorities.
        self._last_queue: list[ActionQueueEntry] = []
        self._last_queue_by_scope: dict[str, list[ActionQueueEntry]] = {}
        # Track whether task mutations since the last cycle require rescoring.
        self._score_cache_dirty: bool = True
        self._dirty_task_ids: set[str] = set()
        # Track current session_id for structured logging
        self._current_session_id: str | None = None
        # Buffer for delegation calls within a single LLM turn (batch dispatch)
        self._turn_delegation_calls: list[dict[str, Any]] = []

        # Tier 1: in-process TTL cache for system_header.md
        self._system_header: str | None = None
        self._system_header_at: float = 0.0

        # --- New intelligence components ---
        self._tool_registry = ToolSetRegistry(
            has_skill_registry=skill_registry is not None,
            has_mcp_registry=mcp_registry is not None,
        )
        if storage_client is not None:
            self._knowledge_base: KnowledgeBase | None = KnowledgeBase(storage_client)
            self._agent_catalog: AgentCatalog | None = AgentCatalog(
                storage_client, redis_client=redis_client
            )
            self._context_manager: ContextManager | None = (
                ContextManager(llm_client) if llm_client is not None else None
            )
        else:
            self._knowledge_base = None
            self._agent_catalog = None
            self._context_manager = None

    @property
    def llm_client(self) -> LLMClient | None:
        """Public accessor for the configured LLM client."""
        return self._llm

    @property
    def graph_repo(self) -> GraphStore:
        """Public accessor for the graph repository dependency."""
        return self._repo

    @property
    def agent_id(self) -> str:
        """Public accessor for this orchestrator's logical agent id."""
        return self._agent_id

    def _invalidate_cached_queue(
        self,
        user_id: str | None,
        dirty_task_ids: set[str] | None = None,
    ) -> None:
        """Invalidate cached scoring queues after task/goal mutations.

        User-scoped caches are preferred in chat flows, but any mutation can
        also affect the global ("__all__") queue.
        """
        self._score_cache_dirty = True
        if dirty_task_ids:
            self._dirty_task_ids.update(dirty_task_ids)
        self._last_queue = []
        if user_id is None:
            self._last_queue_by_scope.clear()
            return
        self._last_queue_by_scope.pop(user_id, None)
        self._last_queue_by_scope.pop("__all__", None)

    async def _invalidate_score_cache_for_task(
        self,
        task_id: str,
        *,
        include_related: bool,
    ) -> None:
        """Invalidate score cache entries affected by a task mutation."""
        cache = getattr(self._engine, "cache", None)
        if cache is None:
            return

        cache.invalidate(task_id)
        if not include_related:
            return

        related_ids: set[str] = set()

        try:
            dependent_edges = await self._repo.get_edges(
                task_id, direction="in", edge_type="DEPENDS_ON"
            )
            dependent_ids = {
                start_id
                for edge in dependent_edges
                for start_id in [edge.get("_start_id")]
                if isinstance(start_id, str) and start_id
            }
            if dependent_ids:
                cache.invalidate_upstream(task_id, list(dependent_ids))
                related_ids.update(dependent_ids)
        except Exception as exc:
            logger.debug(
                "AgentLoop: could not invalidate dependent scores for %s: %s", task_id, exc
            )

        try:
            parent_edges = await self._repo.get_edges(task_id, direction="out", edge_type="PART_OF")
            for edge in parent_edges:
                parent_id = edge.get("_end_id")
                if not parent_id:
                    continue
                cache.invalidate(parent_id)
                related_ids.add(parent_id)
        except Exception as exc:
            logger.debug("AgentLoop: could not invalidate parent scores for %s: %s", task_id, exc)

        if related_ids:
            self._dirty_task_ids.update(related_ids)

    async def _invalidate_score_cache_for_goal(self, goal_id: str) -> None:
        """Invalidate all task scores under a goal after priority/timeline mutations."""
        cache = getattr(self._engine, "cache", None)
        if cache is None:
            return

        try:
            goal_tasks = await self._repo.list_nodes_for_goal(goal_id)
        except Exception as exc:
            logger.debug("AgentLoop: could not list goal tasks for %s: %s", goal_id, exc)
            cache.invalidate_all()
            self._score_cache_dirty = True
            return

        affected_ids: set[str] = set()
        for task in goal_tasks:
            task_id = task.get("id")
            if not task_id:
                continue
            cache.invalidate(task_id)
            affected_ids.add(task_id)

        if affected_ids:
            self._dirty_task_ids.update(affected_ids)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_cycle(
        self,
        user_id: str | None = None,
        trigger_source: str = "heartbeat",
    ) -> list[ActionQueueEntry]:
        """Execute one full agent reasoning cycle.

        Steps
        -----
          1. Fetch active (non-terminal) TaskNode records from the graph.
              If ``user_id`` is provided, the fetch is user-scoped.
        2. Build a ScoringContext by querying relationships for each task.
        3. Score all tasks via ScoringEngine.score_all().
        4. Return the ranked ActionQueueEntry list.

        Returns
        -------
        list[ActionQueueEntry]
            Sorted descending by final_score with rank assigned.
        """
        scope = user_id or "all"
        scope_key = user_id or "__all__"
        logger.info(
            "AgentLoop: starting scoring cycle (scope=%s, trigger_source=%s)",
            scope,
            trigger_source,
        )

        # Heartbeat runs skip work when no mutations have invalidated scores.
        if trigger_source == "heartbeat" and not self._score_cache_dirty:
            cached_queue = self._last_queue_by_scope.get(scope_key)
            if cached_queue:
                logger.debug(
                    "AgentLoop: using cached queue for heartbeat (scope=%s, items=%d)",
                    scope,
                    len(cached_queue),
                )
                return cached_queue

        # 1. Fetch active tasks.
        tasks = await self._fetch_active_tasks(user_id=user_id)
        logger.info("AgentLoop: fetched %d active tasks", len(tasks))

        if not tasks:
            return []

        # 2. Build scoring context.
        context = await self.build_scoring_context(tasks)

        # 3. Score all tasks and return.
        queue = await self._engine.score_all(tasks, context)
        self._last_queue = queue
        self._last_queue_by_scope[scope_key] = queue
        self._score_cache_dirty = False
        self._dirty_task_ids.clear()
        logger.info("AgentLoop: scoring cycle complete — %d items in queue", len(queue))

        logger.info(
            "agent.scoring_cycle",
            extra={
                "event_type": "agent.scoring_cycle",
                "user_id": user_id or "system",
                "tasks_scored": len(tasks),
                "top_task_id": queue[0].node_id if queue else None,
                "queue_depth": len(queue),
                "trigger_source": trigger_source,
            },
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
        channel: str = "cockpit",
        thread_id: str | None = None,
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
        channel:
            Channel the message originated from (default ``"cockpit"``).
            Used for post-turn distillation.
        thread_id:
            Channel-specific thread/conversation handle.  When provided,
            outbound replies are delivered on this thread.

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

        # Reset tool registry to core-only for this message
        self._tool_registry.reset_session()

        # Build system prompt (goal-first, user-scoped)
        system_prompt = await self._build_system_prompt(user_id)

        # Compress conversation history if context manager is available
        current_user_msg = LLMMessage(role="user", content=text)
        if self._context_manager is not None and conversation_history:
            compressed = await self._context_manager.compress(
                conversation_history,
                current_messages=[current_user_msg],
            )
            history_messages = self._context_manager.build_messages(compressed)
        else:
            # Simple role remap: "agent" → "assistant"
            history_messages = []
            for entry in conversation_history or []:
                role = entry.get("role", "user")
                if role == "agent":
                    role = "assistant"
                content = entry.get("content", "")
                if content:
                    history_messages.append(LLMMessage(role=role, content=content))

        messages: list[LLMMessage] = (
            [LLMMessage(role="system", content=system_prompt)]
            + history_messages
            + [current_user_msg]
        )

        # Agentic loop: call LLM → execute tools → call LLM again until no more tool calls
        for _iteration in range(15):  # safety cap on tool-call rounds
            # Get current active tools from registry (updated as sets are loaded)
            tools = self._tool_registry.get_active_tools()

            t0 = time.monotonic()
            response = await self._llm.complete(
                messages,
                model=None,  # use client default
                max_tokens=4096,
                temperature=0.7,
                tools=tools,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            # Log LLM response
            if response.usage:
                logger.info(
                    "agent.message",
                    extra={
                        "event_type": "agent.message",
                        "session_id": session_id or "",
                        "user_id": user_id,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "latency_ms": int(elapsed_ms),
                    },
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

            # No more tool calls — run post-turn distillation (FR-CA-002), then return
            reply = response.content or "(no response)"
            asyncio.ensure_future(
                self._run_distillation(
                    user_id=user_id,
                    text=text,
                    reply=reply,
                    channel=channel,
                    thread_id=thread_id,
                    session_id=session_id,
                )
            )
            return reply

        # Fallback if loop exhausted
        return "(agent tool-call loop limit reached — please try again)"

    def process_chat_message_stream(
        self,
        user_id: str,
        text: str,
        conversation_history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        publisher: UserEventPublisher | None = None,
        channel: str = "cockpit",
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentRunEvent]:
        """Return an async iterator that streams agent run-trace events.

        Yields ``AgentRunEvent`` objects in chronological order.  The run
        always terminates with either ``run.completed`` or ``run.failed``.

        The caller is responsible for consuming the iterator to completion;
        otherwise the underlying LLM stream is not closed cleanly.

        Usage::

            async for event in loop.process_chat_message_stream(user_id, text):
                ...

        Parameters
        ----------
        user_id:
            The authenticated user's ID.
        text:
            Incoming user message text.
        conversation_history:
            Optional prior messages as ``{"role": str, "content": str}`` dicts.
        session_id:
            Optional session ID for structured logging.
        publisher:
            Optional ``UserEventPublisher`` to push events to in parallel
            (e.g. for Redis-backed cockpit delivery).  If ``None``, the
            instance-level ``self._event_publisher`` is used instead.
        channel:
            Channel the message originated from (default ``"cockpit"``).
        thread_id:
            Channel-specific thread/conversation handle.
        """
        return self._process_chat_message_stream_impl(
            user_id=user_id,
            text=text,
            conversation_history=conversation_history,
            session_id=session_id,
            publisher=publisher or self._event_publisher,
            channel=channel,
            thread_id=thread_id,
        )

    async def _process_chat_message_stream_impl(
        self,
        user_id: str,
        text: str,
        conversation_history: list[dict[str, Any]] | None,
        session_id: str | None,
        publisher: UserEventPublisher | None,
        channel: str = "cockpit",
        thread_id: str | None = None,
    ) -> None:
        """Async generator implementing process_chat_message_stream — yields AgentRunEvent."""
        import time as _time

        from graphclaw.agent.run_events import (
            AssistantDeltaPayload,
            AssistantFinalPayload,
            RunCompletedPayload,
            RunFailedPayload,
            RunStartedPayload,
            ToolCompletedPayload,
            ToolFailedPayload,
            ToolStartedPayload,
            make_event,
            new_run_id,
            sanitize_args,
            sanitize_text,
        )
        from graphclaw.agent.run_events import RunEventType as ET
        from graphclaw.llm.base import LLMMessage

        run_id = new_run_id()
        sid = session_id or ""
        seq = 0
        run_start_ms = _time.monotonic()
        tool_call_count = 0
        total_input_tokens = 0
        total_output_tokens = 0

        async def _emit(event: AgentRunEvent) -> None:
            nonlocal seq
            if publisher is not None:
                await publisher.publish(user_id, event)

        # ── run.started ──────────────────────────────────────────────────────
        started_event = make_event(
            ET.RUN_STARTED,
            run_id,
            sid,
            user_id,
            seq,
            RunStartedPayload(message_preview=text[:100]),
        )
        seq += 1
        await _emit(started_event)
        yield started_event

        if self._llm is None:
            failed_event = make_event(
                ET.RUN_FAILED,
                run_id,
                sid,
                user_id,
                seq,
                RunFailedPayload(
                    error_class="NotInitialised",
                    error_message="LLM client is not connected.",
                    duration_ms=int((_time.monotonic() - run_start_ms) * 1000),
                ),
            )
            await _emit(failed_event)
            yield failed_event
            return

        try:
            # Reset tool registry
            self._tool_registry.reset_session()
            self._current_session_id = session_id

            system_prompt = await self._build_system_prompt(user_id)

            current_user_msg = LLMMessage(role="user", content=text)
            if self._context_manager is not None and conversation_history:
                compressed = await self._context_manager.compress(
                    conversation_history,
                    current_messages=[current_user_msg],
                )
                history_messages = self._context_manager.build_messages(compressed)
            else:
                history_messages = []
                for entry in conversation_history or []:
                    role = entry.get("role", "user")
                    if role == "agent":
                        role = "assistant"
                    content = entry.get("content", "")
                    if content:
                        history_messages.append(LLMMessage(role=role, content=content))

            messages: list[LLMMessage] = (
                [LLMMessage(role="system", content=system_prompt)]
                + history_messages
                + [current_user_msg]
            )

            # ── Agentic loop ─────────────────────────────────────────────────
            for _iteration in range(15):
                tools = self._tool_registry.get_active_tools()

                # --- Stream LLM response ---
                accumulated_text = ""
                final_response = None
                t0 = _time.monotonic()

                async for chunk in self._llm.stream(
                    messages,
                    model=None,
                    max_tokens=4096,
                    temperature=0.7,
                    tools=tools,
                ):
                    if chunk.is_final:
                        final_response = chunk.accumulated
                    elif chunk.content_delta:
                        accumulated_text += chunk.content_delta
                        delta_event = make_event(
                            ET.ASSISTANT_DELTA,
                            run_id,
                            sid,
                            user_id,
                            seq,
                            AssistantDeltaPayload(delta=chunk.content_delta),
                        )
                        seq += 1
                        await _emit(delta_event)
                        yield delta_event

                if final_response is None:
                    # Stream did not produce a final chunk — no token counts available
                    pass

                elapsed_ms = int((_time.monotonic() - t0) * 1000)

                if final_response is not None:
                    total_input_tokens += final_response.prompt_tokens
                    total_output_tokens += final_response.completion_tokens

                # ── Tool-use branch ──────────────────────────────────────────
                tool_calls = final_response.tool_calls if final_response else []

                if tool_calls:
                    # Pre-plan delegation if needed
                    self._turn_delegation_calls = []
                    await self._pre_plan_delegation_turn(
                        user_id, sid or str(uuid.uuid4()), tool_calls
                    )

                    messages.append(
                        LLMMessage(
                            role="assistant",
                            content=accumulated_text,
                            tool_calls=list(tool_calls),
                        )
                    )

                    for tc in tool_calls:
                        tool_call_count += 1
                        t_start = make_event(
                            ET.TOOL_STARTED,
                            run_id,
                            sid,
                            user_id,
                            seq,
                            ToolStartedPayload(
                                tool_name=tc.name,
                                args_summary=sanitize_args(tc.arguments),
                            ),
                        )
                        seq += 1
                        await _emit(t_start)
                        yield t_start

                        t0_tool = _time.monotonic()
                        try:
                            tool_result = await self._execute_tool(user_id, tc.name, tc.arguments)
                            latency = int((_time.monotonic() - t0_tool) * 1000)
                            t_done = make_event(
                                ET.TOOL_COMPLETED,
                                run_id,
                                sid,
                                user_id,
                                seq,
                                ToolCompletedPayload(
                                    tool_name=tc.name,
                                    latency_ms=latency,
                                    result_summary=sanitize_text(str(tool_result), 300),
                                ),
                            )
                            seq += 1
                            await _emit(t_done)
                            yield t_done
                            messages.append(
                                LLMMessage(
                                    role="tool",
                                    content=json.dumps(tool_result),
                                    tool_call_id=tc.id,
                                )
                            )
                        except Exception as tc_exc:  # noqa: BLE001
                            latency = int((_time.monotonic() - t0_tool) * 1000)
                            t_fail = make_event(
                                ET.TOOL_FAILED,
                                run_id,
                                sid,
                                user_id,
                                seq,
                                ToolFailedPayload(
                                    tool_name=tc.name,
                                    error_class=type(tc_exc).__name__,
                                    error_message=sanitize_text(str(tc_exc), 200),
                                ),
                            )
                            seq += 1
                            await _emit(t_fail)
                            yield t_fail
                            messages.append(
                                LLMMessage(
                                    role="tool",
                                    content=json.dumps({"error": str(tc_exc)}),
                                    tool_call_id=tc.id,
                                )
                            )

                    self._turn_delegation_calls = []
                    continue  # next iteration — get response after tool results

                # ── Text-only branch — emit assistant.final then run.completed ─
                final_event = make_event(
                    ET.ASSISTANT_FINAL,
                    run_id,
                    sid,
                    user_id,
                    seq,
                    AssistantFinalPayload(
                        content_length=len(accumulated_text),
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                    ),
                )
                seq += 1
                await _emit(final_event)
                yield final_event

                completed_event = make_event(
                    ET.RUN_COMPLETED,
                    run_id,
                    sid,
                    user_id,
                    seq,
                    RunCompletedPayload(
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        tool_call_count=tool_call_count,
                        duration_ms=int((_time.monotonic() - run_start_ms) * 1000),
                    ),
                )
                await _emit(completed_event)
                yield completed_event
                return

            # Safety cap reached
            cap_event = make_event(
                ET.RUN_FAILED,
                run_id,
                sid,
                user_id,
                seq,
                RunFailedPayload(
                    error_class="ToolLoopCapReached",
                    error_message="Agent tool-call loop limit (15) reached.",
                    duration_ms=int((_time.monotonic() - run_start_ms) * 1000),
                ),
            )
            await _emit(cap_event)
            yield cap_event

        except Exception as exc:  # noqa: BLE001
            logger.exception("AgentLoop.stream: unhandled error for user_id=%s", user_id)
            err_event = make_event(
                ET.RUN_FAILED,
                run_id,
                sid,
                user_id,
                seq,
                RunFailedPayload(
                    error_class=type(exc).__name__,
                    error_message=sanitize_text(str(exc), 200),
                    duration_ms=int((_time.monotonic() - run_start_ms) * 1000),
                ),
            )
            await _emit(err_event)
            yield err_event

    # ------------------------------------------------------------------
    # Post-turn distillation (FR-CA-002)
    # ------------------------------------------------------------------

    async def _run_distillation(
        self,
        *,
        user_id: str,
        text: str,
        reply: str,
        channel: str,
        thread_id: str | None,
        session_id: str | None,
    ) -> None:
        """Fire-and-forget post-turn distillation.  Never raises."""
        if self._llm is None or self._storage is None:
            return
        try:
            from graphclaw.agent.distillation import (  # noqa: PLC0415
                DistillationHelper,
                DistillationInput,
            )

            helper = DistillationHelper(
                llm=self._llm,
                graph_repo=getattr(self, "_graph_repo", None),
                storage=self._storage,
            )
            inp = DistillationInput(
                user_id=user_id,
                agent_id=self._agent_id,
                user_text=text,
                agent_reply=reply,
                channel=channel,
                session_id=session_id,
            )
            await helper.distill(inp)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "orchestrator.distillation_failed: %s",
                exc,
                extra={"user_id": user_id, "session_id": session_id or ""},
            )

    # ------------------------------------------------------------------
    # Counterparty conversation mode (FR-CA-003)
    # ------------------------------------------------------------------

    async def process_counterparty_turn(
        self,
        user_id: str,
        counterparty_id: str,
        text: str,
        channel: str,
        thread_id: str,
        session_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Handle a turn from a counterparty (external contact) — not the owner.

        Enters ``counterparty_conversation`` mode:
        - Restricted tool set (see ``COUNTERPARTY_ALLOWED_TOOL_NAMES``).
        - Counterparty-specific system prompt (system_header_counterparty.md).
        - Delegation policy gating on ``update_task_state``.

        Parameters
        ----------
        user_id:
            Owner user ID — who the agent represents.
        counterparty_id:
            Graph node ID of the counterparty (``ResourceNode`` or ``UserNode``).
        text:
            Incoming counterparty message text.
        channel:
            Channel identifier (e.g. ``"telegram"``, ``"email"``).
        thread_id:
            Channel thread/conversation handle.
        session_id:
            Optional session ID for tracing.
        conversation_history:
            Optional prior messages as ``{"role": str, "content": str}`` dicts.

        Returns
        -------
        str
            The agent's reply (after tool round-trips).
        """
        from graphclaw.llm.base import LLMMessage  # noqa: PLC0415

        if self._llm is None:
            return "I'm not fully initialised yet — the language model is not connected."

        # Store session_id for tool execution logging
        self._current_session_id = session_id

        # Reset tool registry to core (counterparty mode filters further)
        self._tool_registry.reset_session()

        # Build counterparty system prompt
        system_prompt = await self._build_counterparty_system_prompt(user_id)

        # Remap "agent" → "assistant" in history
        current_user_msg = LLMMessage(role="user", content=text)
        history_messages: list[LLMMessage] = []
        for entry in conversation_history or []:
            role = entry.get("role", "user")
            if role == "agent":
                role = "assistant"
            content = entry.get("content", "")
            if content:
                history_messages.append(LLMMessage(role=role, content=content))

        messages: list[LLMMessage] = (
            [LLMMessage(role="system", content=system_prompt)]
            + history_messages
            + [current_user_msg]
        )

        for _iteration in range(10):
            # Get tools filtered to counterparty_conversation allow-list
            tools = self._tool_registry.get_active_tools(mode="counterparty_conversation")

            response = await self._llm.complete(
                messages,
                model=None,
                max_tokens=2048,
                temperature=0.5,
                tools=tools,
            )

            if response.tool_calls:
                from graphclaw.agent.tool_registry import (  # noqa: PLC0415
                    COUNTERPARTY_ALLOWED_TOOL_NAMES,
                )

                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=list(response.tool_calls),
                    )
                )
                for tc in response.tool_calls:
                    # Gate: reject tools not in the counterparty allow-list
                    if tc.name not in COUNTERPARTY_ALLOWED_TOOL_NAMES:
                        tool_result = {
                            "error": "ToolNotAvailableInMode",
                            "detail": (
                                f"Tool '{tc.name}' is not available in "
                                "counterparty_conversation mode."
                            ),
                        }
                    else:
                        tool_result = await self._execute_tool(user_id, tc.name, tc.arguments)
                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=json.dumps(tool_result),
                            tool_call_id=tc.id,
                        )
                    )
                continue

            reply = response.content or "(no response)"
            # Post-turn distillation (non-blocking)
            asyncio.ensure_future(
                self._run_distillation(
                    user_id=user_id,
                    text=text,
                    reply=reply,
                    channel=channel,
                    thread_id=thread_id,
                    session_id=session_id,
                )
            )
            return reply

        return "(agent tool-call loop limit reached)"

    async def _build_counterparty_system_prompt(self, user_id: str) -> str:
        """Build system prompt for counterparty_conversation mode."""
        import datetime as _dt  # noqa: PLC0415

        today = _dt.date.today().isoformat()

        # Load counterparty header from storage (fall back to hardcoded)
        header = ""
        if self._storage is not None:
            try:
                from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

                raw = await self._storage.read(
                    StoragePaths.system_prompt_header().replace(
                        "system_header.md", "system_header_counterparty.md"
                    )
                )
                header = raw.decode(errors="replace")
            except Exception:  # noqa: BLE001
                pass

        if not header:
            header = (
                "You are a professional communication agent representing the owner. "
                "You are in counterparty_conversation mode with restricted tools."
            )

        return f"{header}\n\nToday's date is {today}."

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    async def _build_system_prompt(self, user_id: str) -> str:
        """Build a system prompt combining header, agent profile, and graph summary."""
        import datetime as _dt

        today = _dt.date.today().isoformat()
        date_line = f"\nToday's date is {today}. Use this as the reference for all scheduling and deadline reasoning."

        # 1. Load system header from storage (fallback to hardcoded default)
        header = await self._load_system_header()
        parts: list[str] = [header + date_line]

        # 2. Load user agent persona from profile.md
        persona = await self._load_agent_profile(user_id)
        if persona:
            parts.append(f"\n## Your Persona\n{persona}")

        # 3. Tool set manifest (compact ~150 tokens, replaces sending all schemas)
        parts.append(f"\n{self._tool_registry.get_manifest()}")

        # 4. Knowledge base index (available topics)
        if self._knowledge_base is not None:
            kb_index = await self._knowledge_base.get_index()
            if kb_index:
                parts.append(f"\n{kb_index}")

        # 5. Agent catalog (system + user agents available for delegation)
        if self._agent_catalog is not None:
            agent_catalog = await self._agent_catalog.get_compact_catalog(user_id)
            if agent_catalog:
                parts.append(f"\n{agent_catalog}")

        # 6. Add available skills and MCP context
        exec_ctx = await self._build_execution_context(user_id)
        if exec_ctx:
            parts.append(f"\n{exec_ctx}")

        # 7. Goal-first task graph summary (user-scoped)
        graph_summary = await self._build_graph_summary(user_id)
        if graph_summary:
            parts.append(f"\n## Current Task Graph Summary\n{graph_summary}")

        return "\n".join(parts)

    async def _load_system_header(self) -> str:
        """Load system_header.md with a 1-hour in-process TTL cache.

        Falls back to the hardcoded default when storage is unavailable or the
        file does not exist.  The cached value is refreshed after TTL expires
        without requiring a restart (useful for live header updates).
        """
        now = time.monotonic()
        if (
            self._system_header is not None
            and now - self._system_header_at < self._SYSTEM_HEADER_TTL
        ):
            return self._system_header

        if self._storage is None:
            return _SYSTEM_PROMPT_HEADER
        try:
            from graphclaw.infra.storage import StoragePaths

            raw = await self._storage.read(StoragePaths.system_prompt_header())
            header = raw.decode(errors="replace")
        except Exception:  # noqa: BLE001
            header = _SYSTEM_PROMPT_HEADER

        self._system_header = header
        self._system_header_at = now
        return header

    async def _load_agent_profile(self, user_id: str) -> str:
        """Load profile.md with a 15-minute Redis cache (Tier 2).

        Falls back to a direct MinIO read when Redis is unavailable.
        Returns empty string on any storage failure.
        """
        if self._storage is None:
            return ""

        key = f"{self._USER_PROFILE_KEY_PREFIX}{user_id}"

        if self._redis is not None:
            try:
                cached = await self._redis.get(key)
                if cached is not None:
                    return cached
            except Exception as exc:
                logger.warning(
                    "orchestrator.profile_cache.redis_get_failed",
                    extra={"user_id": user_id, "error": str(exc)},
                )

        try:
            from graphclaw.infra.storage import StoragePaths

            path = StoragePaths.agent_profile(user_id, self._agent_id)
            raw = await self._storage.read(path)
            profile = raw.decode(errors="replace")
        except Exception as exc:
            logger.debug("AgentLoop: could not load agent profile: %s", exc)
            return ""

        if self._redis is not None:
            try:
                await self._redis.setex(key, self._USER_PROFILE_REDIS_TTL, profile)
            except Exception as exc:
                logger.warning(
                    "orchestrator.profile_cache.redis_set_failed",
                    extra={"user_id": user_id, "error": str(exc)},
                )

        return profile

    async def invalidate_user_profile(self, user_id: str) -> None:
        """Evict the Redis cache entry for *user_id*'s agent profile.

        Call this from the update-profile API endpoint so the next chat turn
        loads the updated profile.md immediately.  Does nothing when Redis is
        unavailable.
        """
        if self._redis is None:
            return
        key = f"{self._USER_PROFILE_KEY_PREFIX}{user_id}"
        try:
            await self._redis.delete(key)
            logger.debug(
                "orchestrator.invalidate_user_profile",
                extra={"user_id": user_id},
            )
        except Exception as exc:
            logger.warning(
                "orchestrator.invalidate_user_profile.error",
                extra={"user_id": user_id, "error": str(exc)},
            )

    async def _build_graph_summary(self, user_id: str) -> str:
        """Build a goal-first, user-scoped task graph snapshot (§12.4).

        Strategy:
        1. Load active GoalNode summaries for the user; split ACTIVE vs ON_HOLD.
        2. Load top-5 scored tasks; enrich each with task_type, assignee name,
           and blocked-by ID when state=BLOCKED.
        3. Omit COMPLETE/ABANDONED goals entirely.
        """
        from graphclaw.models.enums import GoalState, TaskState

        parts: list[str] = []

        # --- Goals section ---
        try:
            goal_props = await self._repo.list_nodes_by_user("GoalNode", user_id)
            active_goals: list[dict] = []
            on_hold_goals: list[dict] = []
            for gp in goal_props:
                state = gp.get("state", "")
                if state in (GoalState.COMPLETE.value, "ABANDONED", GoalState.OBSOLETE.value):
                    continue
                if state == GoalState.ON_HOLD.value:
                    on_hold_goals.append(gp)
                else:
                    active_goals.append(gp)

            def _format_goal_line(gp: dict) -> str:
                title = gp.get("title", gp.get("id", "?"))
                gid = gp.get("id", "")
                priority = gp.get("priority", "")
                state = gp.get("state", "")
                # Progress fields
                progress = gp.get("progress", {}) or {}
                if isinstance(progress, dict):
                    pct = progress.get("derived_percentage", "")
                    m_done = progress.get("milestones_done", "")
                    m_total = progress.get("milestone_count", "")
                else:
                    pct = m_done = m_total = ""
                # Timeline
                timeline = gp.get("timeline", {}) or {}
                target_date = ""
                if isinstance(timeline, dict):
                    td = (
                        timeline.get("target_date")
                        or timeline.get("due_date")
                        or gp.get("due_date")
                        or gp.get("target_date", "")
                    )
                    if td:
                        target_date = str(td)[:10]  # ISO date only
                parts_line: list[str] = [f"- {title} [{gid}]", priority, state]
                if target_date:
                    parts_line.append(f"due {target_date}")
                if m_done != "" and m_total != "":
                    parts_line.append(f"{m_done}/{m_total} milestones")
                if pct != "":
                    parts_line.append(f"{pct}%")
                return " | ".join(parts_line)

            if active_goals:
                goal_lines = ["### Active Goals"]
                for gp in active_goals[:5]:
                    goal_lines.append(_format_goal_line(gp))
                parts.append("\n".join(goal_lines))

            if on_hold_goals:
                hold_lines = ["### On Hold Goals"]
                for gp in on_hold_goals[:3]:
                    hold_lines.append(_format_goal_line(gp))
                parts.append("\n".join(hold_lines))

        except Exception as exc:  # noqa: BLE001
            logger.debug("AgentLoop: goal summary failed: %s", exc)

        # --- Top priority tasks section ---
        scoped_queue = self._last_queue_by_scope.get(user_id, [])
        if not scoped_queue:
            try:
                scoped_queue = await self.run_cycle(user_id=user_id, trigger_source="on_demand")
            except Exception as exc:  # noqa: BLE001
                logger.debug("AgentLoop: scoring cycle for graph summary failed: %s", exc)

        if scoped_queue:
            try:
                tasks = await self._fetch_active_tasks(user_id)
                task_index = {t.id: t for t in tasks}
            except Exception:  # noqa: BLE001
                task_index = {}

            # Collect assignee IDs and blocker IDs so we can bulk-resolve names.
            assignee_ids: set[str] = set()
            blocker_map: dict[str, str] = {}  # task_id → blocker_task_id
            for entry in scoped_queue[:5]:
                task = task_index.get(entry.node_id)
                if task is None:
                    continue
                if task.assigned_to:
                    assignee_ids.add(task.assigned_to)
                if task.state == TaskState.BLOCKED:
                    try:
                        in_edges = await self._repo.get_edges(
                            task.id, direction="in", edge_type="BLOCKS"
                        )
                        if in_edges:
                            blocker_id = in_edges[0].get("_start_id", "")
                            if blocker_id:
                                blocker_map[task.id] = blocker_id
                    except Exception:  # noqa: BLE001
                        pass

            # Bulk-resolve assignee names in one graph call.
            assignee_names: dict[str, str] = {}
            if assignee_ids:
                try:
                    nodes = await self._repo.get_nodes_bulk(list(assignee_ids))
                    for nid, props in nodes.items():
                        name = props.get("name") or props.get("title") or nid
                        assignee_names[nid] = name
                except Exception:  # noqa: BLE001
                    pass

            task_lines = ["### Top Priority Tasks"]
            for entry in scoped_queue[:5]:
                task = task_index.get(entry.node_id)
                if task is None:
                    continue

                task_type = task.task_type.value if task.task_type else ""
                state_str = task.state.value if task.state else str(task.state)

                # Assignee short name
                assignee_part = ""
                if task.assigned_to:
                    raw_name = assignee_names.get(task.assigned_to, task.assigned_to)
                    short_name = raw_name.split()[0] if raw_name else task.assigned_to
                    assignee_part = f" | @{short_name}"

                # Deadline
                deadline_part = ""
                if task.timeline and task.timeline.deadline:
                    deadline_part = f" | due {task.timeline.deadline.date()}"

                # Blocked-by
                if task.state == TaskState.BLOCKED and task.id in blocker_map:
                    state_str = f"BLOCKED by {blocker_map[task.id]}"

                task_lines.append(
                    f"- [{entry.rank}] {task.title} [{task.id}]"
                    f" | {task_type} | {state_str}"
                    f" | score={entry.final_score:.2f}"
                    f"{assignee_part}{deadline_part}"
                )

            if len(task_lines) > 1:
                parts.append("\n".join(task_lines))

        return "\n\n".join(parts) if parts else "No active goals or tasks found."

    # ------------------------------------------------------------------
    # Tool definitions and execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, user_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a tool call and return the result as a dict."""
        t0 = time.monotonic()
        result: dict[str, Any] = {}
        try:
            # --- Core tools (always active) ---
            if name == "list_tasks":
                result = await self._tool_list_tasks(user_id, arguments)
            elif name == "get_task_details":
                result = await self._tool_get_task_details(user_id, arguments)
            elif name == "update_task_state":
                result = await self._tool_update_task_state(user_id, arguments)
            elif name == "list_available_agents":
                result = await self._tool_list_available_agents(user_id, arguments)
            elif name == "load_tool_set":
                result = await self._tool_load_tool_set(arguments)
            elif name == "read_knowledge":
                result = await self._tool_read_knowledge(arguments)
            # --- task_management set ---
            elif name == "create_goal":
                result = await self._tool_create_goal(user_id, arguments)
            elif name == "create_task":
                result = await self._tool_create_task(user_id, arguments)
            elif name == "update_task":
                result = await self._tool_update_task(user_id, arguments)
            elif name == "update_goal":
                result = await self._tool_update_goal(user_id, arguments)
            # Wave 0 (FR-DEL-002): Archive tools — replace delete_*
            elif name == "archive_task":
                result = await self._tool_archive_task(user_id, arguments)
            elif name == "archive_resource":
                result = await self._tool_archive_resource(user_id, arguments)
            elif name == "archive_goal":
                result = await self._tool_archive_goal(user_id, arguments)
            # --- planning set ---
            elif name == "propose_plan":
                result = await self._tool_propose_plan(user_id, arguments)
            elif name == "edit_plan":
                result = await self._tool_edit_plan(user_id, arguments)
            elif name == "approve_plan":
                result = await self._tool_approve_plan(user_id, arguments)
            elif name == "execute_plan":
                result = await self._tool_execute_plan(user_id, arguments)
            elif name == "propose_goal_inference":
                result = await self._tool_propose_goal_inference(user_id, arguments)
            elif name == "approve_goal_inference":
                result = await self._tool_approve_goal_inference(user_id, arguments)
            # --- skills set ---
            elif name == "list_available_skills":
                result = await self._tool_list_available_skills(user_id, arguments)
            elif name == "invoke_skill":
                result = await self._tool_invoke_skill(user_id, arguments)
            # --- mcp set ---
            elif name == "list_mcp_tools":
                result = await self._tool_list_mcp_tools(user_id, arguments)
            elif name == "call_mcp_tool":
                result = await self._tool_call_mcp_tool(user_id, arguments)
            # --- delegation set ---
            elif name == "delegate_to_agent":
                result = await self._tool_delegate_to_agent(user_id, arguments)
            elif name == "create_agent":
                result = await self._tool_create_agent(user_id, arguments)
            # --- identity set (FR-ID-002..005) ---
            elif name == "resolve_user":
                result = await self._tool_resolve_user(user_id, arguments)
            elif name == "start_create_person_dialog":
                result = await self._tool_start_create_person_dialog(user_id, arguments)
            elif name == "respond_to_create_person_dialog":
                result = await self._tool_respond_to_create_person_dialog(user_id, arguments)
            elif name == "merge_resource":
                result = await self._tool_merge_resource(user_id, arguments)
            elif name == "register_alias":
                result = await self._tool_register_alias(user_id, arguments)
            # --- onboarding set (FR-ID-001) ---
            elif name == "set_user_name":
                result = await self._tool_set_user_name(user_id, arguments)
            elif name == "set_user_persona":
                result = await self._tool_set_user_persona(user_id, arguments)
            elif name == "add_user_identity":
                result = await self._tool_add_user_identity(user_id, arguments)
            elif name == "set_working_hours":
                result = await self._tool_set_working_hours(user_id, arguments)
            elif name == "set_preferences":
                result = await self._tool_set_preferences(user_id, arguments)
            elif name == "seed_policy_from_template":
                result = await self._tool_seed_policy_from_template(user_id, arguments)
            elif name == "complete_onboarding":
                result = await self._tool_complete_onboarding(user_id, arguments)
            else:
                result = {"error": f"Unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentLoop: tool %s raised %s", name, exc)
            result = {"error": str(exc)}

        event = AgentToolCallEvent(
            tool_name=name,
            user_id=user_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
            session_id=self._current_session_id or "",
            task_id=self._extract_task_id(arguments),
            success=self._is_tool_call_success(result),
            attempt=self._coerce_attempt(arguments.get("attempt", 1)),
        )
        logger.info(
            "agent.tool_call",
            extra={"event_type": "agent.tool_call", **event.model_dump()},
        )
        return result

    @staticmethod
    def _is_tool_call_success(result: dict[str, Any]) -> bool:
        """Return whether a tool result should be counted as success."""
        if bool(result.get("error")):
            return False
        if "success" in result and result.get("success") is False:
            return False
        return True

    @staticmethod
    def _extract_task_id(arguments: dict[str, Any]) -> str | None:
        """Best-effort extraction of task identifiers from tool args."""
        for key in ("task_id", "id", "source_task_id", "target_task_id", "approval_task_id"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _coerce_attempt(value: Any) -> int:
        """Normalize attempt from tool args, defaulting to first attempt."""
        try:
            attempt = int(value)
        except (TypeError, ValueError):
            return 1
        return attempt if attempt > 0 else 1

    async def _tool_load_tool_set(self, args: dict[str, Any]) -> dict[str, Any]:
        """Activate a named tool set for the current session."""
        set_name = args.get("name", "")
        # C4: this method has no user_id; scoping validation is done at call-site via agent_id.
        # Callers that have user_id should call _tool_load_tool_set_scoped instead.
        tools = self._tool_registry.activate(set_name)
        if not tools:
            return {
                "error": f"Tool set '{set_name}' is not available. "
                f"Available sets: {', '.join(['task_management', 'planning', 'skills', 'mcp', 'delegation'])}",
            }
        return {
            "activated": set_name,
            "tools_available": [t.name for t in tools],
            "message": f"Tool set '{set_name}' activated — {len(tools)} tool(s) now available.",
        }

    async def _tool_read_knowledge(self, args: dict[str, Any]) -> dict[str, Any]:
        """Load a domain knowledge document from the system knowledge base."""
        topic = args.get("topic", "")
        if self._knowledge_base is None:
            return {"error": "Knowledge base not available (storage not configured)."}
        content = await self._knowledge_base.read(topic)
        return {"topic": topic, "content": content}

    async def _tool_list_available_agents(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """List all agents available for delegation, filtered by agent config if scoped."""
        if self._agent_catalog is None:
            return {"agents": [], "note": "Agent catalog not available (storage not configured)."}
        capability_filter = args.get("capability_filter")
        agents = await self._agent_catalog.list_all(user_id, capability_filter=capability_filter)
        # C5: filter by config.json.sub_agents[] if present
        agent_cfg = await self._load_agent_config(user_id, self._agent_id)
        allowed_sub = agent_cfg.get("sub_agents") if agent_cfg else None
        if isinstance(allowed_sub, list):
            agents = [a for a in agents if a.get("agent_id") in allowed_sub]
        return {"agents": agents, "count": len(agents)}

    async def _tool_list_tasks(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """List tasks for the user, with progressive filtering support."""
        from graphclaw.models.enums import TaskState

        _TERMINAL = {TaskState.COMPLETE.value, TaskState.CANCELLED.value, TaskState.SNOOZED.value}

        goal_id = args.get("goal_id")
        state_filter = args.get("state_filter")
        task_type_filter = args.get("task_type")
        limit = min(int(args.get("limit", 10)), 50)
        include_completed = bool(args.get("include_completed", False))
        assigned_to = args.get("assigned_to")
        state_filter_norm = str(state_filter).upper() if state_filter else None
        task_type_filter_norm = (
            str(task_type_filter).upper().replace("_", "") if task_type_filter else None
        )

        def _norm_state(value: Any) -> str:
            return str(getattr(value, "value", value or "")).upper()

        def _norm_task_type(value: Any) -> str:
            return str(getattr(value, "value", value or "")).upper().replace("_", "")

        def _deadline_from_props(task_props: dict[str, Any]) -> str | None:
            timeline = task_props.get("timeline")
            if isinstance(timeline, str) and timeline and timeline[0] in ("{", "["):
                try:
                    timeline = json.loads(timeline)
                except (TypeError, ValueError):
                    timeline = None
            if isinstance(timeline, dict):
                deadline = timeline.get("deadline")
                if deadline:
                    return str(deadline)
            return None

        # Fetch by goal subgraph or full user task list
        if goal_id:
            try:
                raw_nodes = await self._repo.list_nodes_for_goal(goal_id)
                tasks = [_deserialise_graph_props(props) for props in raw_nodes]
            except Exception as exc:
                return {"error": f"Failed to load tasks for goal {goal_id}: {exc}"}
        else:
            raw_nodes = await self._repo.list_nodes_by_user("TaskNode", user_id)
            tasks = [_deserialise_graph_props(props) for props in raw_nodes]

        # Enforce user scope for all list paths, including goal-scoped lookups.
        tasks = [t for t in tasks if t.get("owned_by") == user_id]

        # Apply filters
        if not include_completed:
            tasks = [t for t in tasks if _norm_state(t.get("state")) not in _TERMINAL]
        if state_filter_norm:
            tasks = [t for t in tasks if _norm_state(t.get("state")) == state_filter_norm]
        if task_type_filter_norm:
            tasks = [
                t for t in tasks if _norm_task_type(t.get("task_type")) == task_type_filter_norm
            ]
        if assigned_to:
            tasks = [t for t in tasks if t.get("assigned_to") == assigned_to]

        # Default (non-goal) retrieval is priority-first: order by the latest
        # scored queue for this user, then append any unmatched tasks.
        if not goal_id:
            scored_queue = self._last_queue_by_scope.get(user_id, [])
            if not scored_queue:
                try:
                    scored_queue = await self.run_cycle(user_id=user_id, trigger_source="on_demand")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("AgentLoop: list_tasks scoring fallback failed: %s", exc)
                    scored_queue = []

            if scored_queue:
                tasks_by_id = {str(t.get("id", "")): t for t in tasks}
                ordered: list[dict[str, Any]] = []
                for entry in scored_queue:
                    hit = tasks_by_id.pop(entry.node_id, None)
                    if hit is not None:
                        ordered.append(hit)
                # Keep deterministic order for non-scored leftovers (e.g.
                # include_completed=true items that are intentionally not scored).
                if tasks_by_id:
                    ordered.extend(sorted(tasks_by_id.values(), key=lambda t: str(t.get("id", ""))))
                tasks = ordered
            else:
                tasks = sorted(tasks, key=lambda t: str(t.get("id", "")))

        tasks = tasks[:limit]

        return {
            "tasks": [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "state": t.get("state"),
                    "task_type": str(getattr(t.get("task_type"), "value", t.get("task_type"))),
                    "assigned_to": t.get("assigned_to"),
                    "deadline": _deadline_from_props(t),
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
        self._invalidate_cached_queue(user_id)
        return {"goal_id": goal_id, "title": args["title"], "status": "created"}

    async def _tool_create_task(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.models.base import generate_task_id
        from graphclaw.models.enums import TaskState, TaskType
        from graphclaw.models.nodes import TaskNode

        # Map string task_type to TaskType enum
        _TYPE_MAP: dict[str, TaskType] = {
            "ATOMIC": TaskType.ATOMIC,
            "COMPOSITE": TaskType.COMPOSITE,
            "FOLLOWUP": TaskType.FOLLOWUP,
            "RESEARCH": TaskType.RESEARCH,
            "APPROVAL": TaskType.APPROVAL,
            "MILESTONE": TaskType.MILESTONE,
            "REVIEW": TaskType.REVIEW,
            "RECURRING": TaskType.RECURRING,
            "DECISION": TaskType.DECISION,
            "CHECKIN": TaskType.CHECKIN,
            "DELEGATED": TaskType.DELEGATED,
        }
        task_type_str = str(args.get("task_type", "ATOMIC"))
        task_type_key = task_type_str.upper().replace("_", "")
        task_type = _TYPE_MAP.get(task_type_key, TaskType.ATOMIC)

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
            assigned_to=args.get("assigned_to"),
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
        if args.get("assigned_to"):
            try:
                await self._repo.create_edge(task_id, args["assigned_to"], "ASSIGNED_TO", {})
            except Exception as exc:
                logger.warning("AgentLoop: could not wire ASSIGNED_TO edge: %s", exc)

        # Auto-spawn a FollowUp task for DELEGATED tasks (PRD §3.1, §6.2).
        followup_task_id: str | None = None
        if task_type == TaskType.DELEGATED:
            from graphclaw.models.type_metadata import DelegatedMetadata, FollowUpMetadata
            from graphclaw.triggers.followup import compute_next_followup
            from graphclaw.triggers.models import FollowupConfig

            def _safe_float(value: Any, default: float) -> float:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            def _decode_json_like(value: Any) -> Any:
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except (json.JSONDecodeError, ValueError):
                        return value
                return value

            followup_id = generate_task_id("AG", TaskType.FOLLOWUP)
            base_cadence_days = 3.0
            complexity_factor = 1.0
            reliability_score = 0.8
            recency_bonus = 0.0

            try:
                owner_raw = await self._repo.get_node(user_id)
                if owner_raw:
                    prefs = _decode_json_like(owner_raw.get("preferences"))
                    if isinstance(prefs, dict):
                        base_cadence_days = max(
                            0.1,
                            _safe_float(prefs.get("default_follow_up_days"), base_cadence_days),
                        )
            except Exception as exc:
                logger.debug("AgentLoop: could not load owner preferences for %s: %s", user_id, exc)

            assignee_id = args.get("assigned_to")
            if assignee_id:
                try:
                    assignee_raw = await self._repo.get_node(assignee_id)
                    if assignee_raw:
                        reliability = _decode_json_like(assignee_raw.get("reliability"))
                        if isinstance(reliability, dict):
                            reliability_score = _safe_float(
                                reliability.get("overall_score"),
                                reliability_score,
                            )
                            recency_bonus = _safe_float(
                                reliability.get("on_time_delivery_rate"),
                                recency_bonus,
                            )
                except Exception as exc:
                    logger.debug(
                        "AgentLoop: could not load assignee reliability for %s: %s",
                        assignee_id,
                        exc,
                    )

            followup_config = FollowupConfig(
                task_id=task_id,
                base_cadence_days=base_cadence_days,
                complexity_factor=max(0.1, complexity_factor),
                reliability_score=max(0.0, min(1.0, reliability_score)),
                recency_bonus=max(0.0, min(1.0, recency_bonus)),
            )
            scheduled_fire_at = compute_next_followup(followup_config)
            followup = TaskNode(
                id=followup_id,
                task_type=TaskType.FOLLOWUP,
                title=f"Follow-up: {args['title']}",
                description=f"Auto-generated follow-up for delegated task {task_id}",
                created_by=user_id,
                owned_by=user_id,
                state=TaskState.INACTIVE_PENDING,
                timeline=Timeline(),
                created_at=now_task,
                updated_at=now_task,
                type_metadata=FollowUpMetadata(
                    target_task_id=task_id,
                    parent_delegated_id=task_id,
                    scheduled_fire_at=scheduled_fire_at,
                ),
            )
            try:
                await self._repo.create_node(followup)
                await self._repo.create_edge(followup_id, task_id, "FOLLOW_UP_FOR", {})
                followup_task_id = followup_id
                del_meta = DelegatedMetadata(
                    assigned_resource_id=args.get("assigned_to") or user_id,
                    follow_up_task_id=followup_id,
                )
                await self._repo.update_node(
                    task_id, {"type_metadata": del_meta.model_dump(mode="json")}
                )
                logger.info(
                    "AgentLoop: auto-spawned follow-up %s for delegated task %s",
                    followup_id,
                    task_id,
                )
            except Exception as exc:
                logger.warning("AgentLoop: could not auto-spawn follow-up for %s: %s", task_id, exc)

        self._invalidate_cached_queue(user_id)

        result = {
            "task_id": task_id,
            "title": args["title"],
            "task_type": str(task_type.value),
            "status": "created",
        }
        if followup_task_id:
            result["follow_up_task_id"] = followup_task_id
        return result

    async def _tool_update_task_state(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.models.deserialization import deserialize_task_node_props
        from graphclaw.models.enums import ChangedBy, TaskState
        from graphclaw.state.cascade import persist_transition_and_cascade
        from graphclaw.state.transitions import InvalidTransitionError

        task_id = args["task_id"]
        props = await self._repo.get_node(task_id)
        if not props:
            return {"error": f"Task {task_id} not found"}

        try:
            target_state = TaskState(args["new_state"].upper())
        except ValueError:
            valid_states = ", ".join(state.value for state in TaskState)
            return {"error": f"Invalid state {args['new_state']!r}. Valid: {valid_states}"}

        try:
            task = TaskNode.model_validate(deserialize_task_node_props(props))
        except Exception as exc:
            return {"error": f"Task {task_id} could not be parsed: {exc}"}

        current_state = task.state.value
        try:
            await persist_transition_and_cascade(
                task,
                target_state,
                ChangedBy.HUMAN,
                args.get("reason", ""),
                self._repo,
                self._sm,
            )
        except InvalidTransitionError as exc:
            return {"error": str(exc)}

        await self._invalidate_score_cache_for_task(task_id, include_related=True)
        self._invalidate_cached_queue(user_id, dirty_task_ids={task_id})
        return {
            "task_id": task_id,
            "old_state": current_state,
            "new_state": target_state.value,
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
        await self._invalidate_score_cache_for_task(task_id, include_related=False)
        self._invalidate_cached_queue(user_id, dirty_task_ids={task_id})
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
        if "priority" in args or "deadline" in args:
            await self._invalidate_score_cache_for_goal(goal_id)
        self._invalidate_cached_queue(user_id)
        changed = [k for k in updates if k != "updated_at"]
        return {"goal_id": goal_id, "status": "updated", "fields_updated": changed}

    # ------------------------------------------------------------------
    # Wave 0 (FR-DEL-002): Archive tool handlers
    # These use self._admin_repo so the lifecycle-field guard (W0-PR4) is
    # satisfied.  The agent_principal store (self._repo) cannot write
    # lifecycle fields.
    # ------------------------------------------------------------------

    async def _tool_archive_task(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.archive import ArchiveError, archive_task  # noqa: PLC0415

        task_id = args.get("task_id")
        reason = args.get("reason", "")
        redirect_to = args.get("redirect_to")
        if not task_id:
            return {"error": "task_id is required"}
        try:
            return await archive_task(
                task_id=task_id,
                archived_by=user_id,
                reason=reason,
                redirect_to=redirect_to,
                admin_store=self._admin_repo,
            )
        except ArchiveError as exc:
            return {"error": str(exc)}

    async def _tool_archive_resource(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.archive import ArchiveError, archive_resource  # noqa: PLC0415

        resource_id = args.get("resource_id")
        reason = args.get("reason", "")
        redirect_to = args.get("redirect_to")
        if not resource_id:
            return {"error": "resource_id is required"}
        try:
            return await archive_resource(
                resource_id=resource_id,
                archived_by=user_id,
                reason=reason,
                redirect_to=redirect_to,
                admin_store=self._admin_repo,
            )
        except ArchiveError as exc:
            return {"error": str(exc)}

    async def _tool_archive_goal(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.archive import ArchiveError, archive_goal  # noqa: PLC0415

        goal_id = args.get("goal_id")
        reason = args.get("reason", "")
        redirect_to = args.get("redirect_to")
        if not goal_id:
            return {"error": "goal_id is required"}
        try:
            return await archive_goal(
                goal_id=goal_id,
                archived_by=user_id,
                reason=reason,
                redirect_to=redirect_to,
                admin_store=self._admin_repo,
            )
        except ArchiveError as exc:
            return {"error": str(exc)}

    async def _tool_get_task_details(self, _user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Return a layered detail view for one task or goal (§12.5).

        Response is ordered from most-actionable to most-historical:
        header → timeline → assignee → goal → dependencies → edges →
        scoring → type_metadata → intelligence log.
        """
        node_id = args.get("node_id") or args.get("task_id", "")
        if not node_id:
            return {"error": "node_id is required"}

        props = await self._repo.get_node(node_id)
        if not props:
            return {"error": f"Node {node_id} not found"}

        raw = _deserialise_graph_props(props)
        is_goal = node_id.upper().startswith("GOAL") or raw.get("node_type", "").startswith("Goal")

        # --- Fetch edges ---
        out_edges: list[dict] = []
        in_edges: list[dict] = []
        try:
            out_edges = await self._repo.get_edges(node_id, direction="out")
            in_edges = await self._repo.get_edges(node_id, direction="in")
        except Exception as exc:
            logger.debug("get_task_details: edge fetch failed for %s: %s", node_id, exc)

        def _label(e: dict) -> str:
            return str(e.get("type") or e.get("_label") or "")

        def _target(e: dict) -> str:
            return str(e.get("target_id") or e.get("_end_id") or "")

        def _source(e: dict) -> str:
            return str(e.get("source_id") or e.get("_start_id") or "")

        depends_on_ids = [_target(e) for e in out_edges if _label(e) == "DEPENDS_ON" and _target(e)]
        blocks_ids = [_target(e) for e in out_edges if _label(e) == "BLOCKS" and _target(e)]
        blocked_by_ids = [_source(e) for e in in_edges if _label(e) == "BLOCKS" and _source(e)]
        part_of_id = next(
            (_target(e) for e in out_edges if _label(e) == "PART_OF" and _target(e)), None
        )
        assignee_id = next(
            (_target(e) for e in out_edges if _label(e) == "ASSIGNED_TO" and _target(e)), None
        )
        spawned_from_id = next(
            (_target(e) for e in out_edges if _label(e) == "SPAWNED_FROM" and _target(e)), None
        )
        depended_on_by_ids = [
            _source(e) for e in in_edges if _label(e) == "DEPENDS_ON" and _source(e)
        ]

        # --- Bulk-resolve neighbor names in one round-trip ---
        neighbor_ids: list[str] = [
            nid
            for nid in [
                *depends_on_ids,
                *blocks_ids,
                *blocked_by_ids,
                *depended_on_by_ids,
                part_of_id,
                assignee_id,
                spawned_from_id,
            ]
            if nid
        ]
        neighbor_props: dict[str, dict] = {}
        if neighbor_ids:
            try:
                neighbor_props = await self._repo.get_nodes_bulk(neighbor_ids)
            except Exception as exc:
                logger.debug("get_task_details: bulk fetch failed: %s", exc)

        def _name(nid: str | None) -> str:
            if not nid:
                return ""
            p = neighbor_props.get(nid, {})
            return p.get("name") or p.get("title") or nid

        def _state(nid: str | None) -> str:
            if not nid:
                return ""
            return str(neighbor_props.get(nid, {}).get("state", ""))

        # --- Find scoring entry from last queue ---
        queue_entry: ActionQueueEntry | None = next(
            (e for e in self._last_queue if e.node_id == node_id), None
        )

        # ================================================================
        # Build the layered response
        # ================================================================
        result: dict[str, Any] = {}

        # --- Header ---
        header: dict[str, Any] = {
            "id": raw.get("id", node_id),
            "title": raw.get("title", ""),
            "state": raw.get("state", ""),
        }
        if not is_goal:
            header["task_type"] = raw.get("task_type", "")
            if queue_entry:
                header["score"] = round(queue_entry.final_score, 3)
                header["rank"] = queue_entry.rank
                header["autonomy_level"] = queue_entry.autonomy_level.value
                header["recommended_action"] = queue_entry.recommended_action
            is_overridden = (raw.get("override") or {}).get("is_overridden", False)
            if is_overridden:
                header["overridden"] = True
        result["header"] = header

        # --- Timeline ---
        timeline_raw = raw.get("timeline") or {}
        if isinstance(timeline_raw, dict):
            tl: dict[str, Any] = {}
            for field in (
                "deadline",
                "target_date",
                "started_at",
                "completed_at",
                "estimated_effort_hours",
                "estimated_effort_days",
                "actual_effort_days",
            ):
                val = timeline_raw.get(field)
                if val is not None:
                    tl[field] = val
            progress_raw = raw.get("progress") or {}
            if isinstance(progress_raw, dict):
                pct = progress_raw.get("percentage") or progress_raw.get("derived_percentage")
                if pct is not None:
                    tl["progress_pct"] = pct
            if tl:
                result["timeline"] = tl

        # --- Assignee ---
        if assignee_id:
            ap = neighbor_props.get(assignee_id, {})
            result["assigned_to"] = {
                "id": assignee_id,
                "name": _name(assignee_id),
                "load_factor": (ap.get("capacity") or {}).get("load_factor"),
                "reliability": (ap.get("reliability") or {}).get("overall_score"),
                "availability": (ap.get("capacity") or {}).get("availability_status"),
            }

        # --- Parent goal ---
        if part_of_id:
            gp = neighbor_props.get(part_of_id, {})
            result["goal"] = {
                "id": part_of_id,
                "title": _name(part_of_id),
                "priority": gp.get("priority"),
                "state": gp.get("state"),
            }

        # --- Dependencies ---
        deps: dict[str, Any] = {}
        if depends_on_ids:
            deps["waiting_on"] = [
                {"id": nid, "title": _name(nid), "state": _state(nid)} for nid in depends_on_ids
            ]
        if depended_on_by_ids:
            deps["blocking"] = [
                {"id": nid, "title": _name(nid), "state": _state(nid)} for nid in depended_on_by_ids
            ]
        if blocked_by_ids:
            deps["blocked_by"] = [
                {"id": nid, "title": _name(nid), "state": _state(nid)} for nid in blocked_by_ids
            ]
        if blocks_ids:
            deps["actively_blocks"] = [
                {"id": nid, "title": _name(nid), "state": _state(nid)} for nid in blocks_ids
            ]
        if deps:
            result["dependencies"] = deps

        # --- Edge summary ---
        edges: dict[str, Any] = {}
        if part_of_id:
            edges["PART_OF"] = part_of_id
        if assignee_id:
            edges["ASSIGNED_TO"] = f"{assignee_id} ({_name(assignee_id)})"
        if depends_on_ids:
            edges["DEPENDS_ON"] = depends_on_ids
        if blocks_ids:
            edges["BLOCKS"] = blocks_ids
        if spawned_from_id:
            edges["SPAWNED_FROM"] = spawned_from_id
        if edges:
            result["edges"] = edges

        # --- Scoring factors ---
        if queue_entry and queue_entry.explanation.factors:
            expl = queue_entry.explanation
            result["scoring"] = {
                "final_score": round(expl.final_score, 3),
                "summary": expl.summary,
                "topology_note": expl.topology_note,
                "factors": [
                    {
                        "factor": f.factor_name,
                        "weighted_score": round(f.weighted_score, 3),
                        "reason": f.plain_english,
                    }
                    for f in sorted(expl.factors, key=lambda f: f.weighted_score, reverse=True)
                ],
            }

        # --- Type metadata (task-specific fields only) ---
        type_meta = raw.get("type_metadata") or {}
        if isinstance(type_meta, dict) and type_meta:
            # Strip internal/empty fields
            clean_meta = {
                k: v for k, v in type_meta.items() if v is not None and v != [] and v != {}
            }
            if clean_meta:
                result["type_metadata"] = clean_meta

        # --- Goal-specific fields (for GoalNode detail) ---
        if is_goal:
            prog = raw.get("progress") or {}
            if isinstance(prog, dict):
                result["progress"] = {k: v for k, v in prog.items() if v is not None}
            constraints_raw = raw.get("constraints") or []
            if constraints_raw:
                result["constraints"] = constraints_raw

        # --- Intelligence log (last 5 entries) ---
        intel = raw.get("intelligence") or ""
        if intel and isinstance(intel, str):
            lines = [ln.strip() for ln in intel.strip().splitlines() if ln.strip()]
            result["intelligence_log"] = lines[-5:]

        # --- Recent state history (last 3 entries) ---
        state_history = raw.get("state_history") or []
        if isinstance(state_history, list) and state_history:
            result["state_history"] = state_history[-3:]

        return result

    # _tool_check_inbox removed — inbox reading is now handled by the comms sub-agent.
    # Delegate to the comms agent via delegate_to_agent(task_id, "comms", instructions).

    # ------------------------------------------------------------------
    # Planning tools
    # ------------------------------------------------------------------

    def _plan_storage_path(self, user_id: str, plan_id: str) -> str:
        from graphclaw.infra.storage import StoragePaths

        return (
            f"{StoragePaths.agent_root(user_id, self._agent_id)}state/pending_plans/{plan_id}.json"
        )

    async def _persist_pending_plan(self, user_id: str, plan_data: dict[str, Any]) -> None:
        plan_id = str(plan_data.get("plan_id", "")).strip()
        if not plan_id:
            return

        if not hasattr(self, "_pending_plans"):
            self._pending_plans: dict[str, dict[str, Any]] = {}
        self._pending_plans[plan_id] = plan_data

        if self._storage:
            try:
                await self._storage.write(
                    self._plan_storage_path(user_id, plan_id),
                    json.dumps(plan_data).encode(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("AgentLoop: could not persist plan to storage: %s", exc)

    async def _load_pending_plan(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        plan_data = getattr(self, "_pending_plans", {}).get(plan_id)
        if plan_data:
            return plan_data

        if self._storage:
            try:
                raw = await self._storage.read(self._plan_storage_path(user_id, plan_id))
                loaded = json.loads(raw.decode())
                if isinstance(loaded, dict):
                    if not hasattr(self, "_pending_plans"):
                        self._pending_plans: dict[str, dict[str, Any]] = {}
                    self._pending_plans[plan_id] = loaded
                    return loaded
            except Exception:  # noqa: BLE001
                return None
        return None

    def _goal_inference_storage_path(self, user_id: str, inference_id: str) -> str:
        from graphclaw.infra.storage import StoragePaths

        return (
            f"{StoragePaths.agent_root(user_id, self._agent_id)}"
            f"state/pending_goal_inferences/{inference_id}.json"
        )

    async def _persist_goal_inference(
        self,
        user_id: str,
        inference_data: dict[str, Any],
    ) -> None:
        inference_id = str(inference_data.get("inference_id", "")).strip()
        if not inference_id:
            return

        if not hasattr(self, "_pending_goal_inferences"):
            self._pending_goal_inferences: dict[str, dict[str, Any]] = {}
        self._pending_goal_inferences[inference_id] = inference_data

        if self._storage:
            try:
                await self._storage.write(
                    self._goal_inference_storage_path(user_id, inference_id),
                    json.dumps(inference_data).encode(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("AgentLoop: could not persist goal inference to storage: %s", exc)

    async def _load_goal_inference(
        self,
        user_id: str,
        inference_id: str,
    ) -> dict[str, Any] | None:
        cached = getattr(self, "_pending_goal_inferences", {}).get(inference_id)
        if cached:
            return cached

        if self._storage:
            try:
                raw = await self._storage.read(
                    self._goal_inference_storage_path(user_id, inference_id)
                )
                loaded = json.loads(raw.decode())
                if isinstance(loaded, dict):
                    if not hasattr(self, "_pending_goal_inferences"):
                        self._pending_goal_inferences: dict[str, dict[str, Any]] = {}
                    self._pending_goal_inferences[inference_id] = loaded
                    return loaded
            except Exception:  # noqa: BLE001
                return None
        return None

    def _build_goal_inference_title(
        self,
        *,
        topic: str,
        assigned_to: str,
        due_bucket: str,
    ) -> str:
        fallback_topics = {
            "atomic",
            "composite",
            "delegated",
            "followup",
            "approval",
            "milestone",
            "review",
            "recurring",
            "decision",
            "checkin",
            "research",
        }
        if topic and topic not in fallback_topics:
            return f"{topic.replace('_', ' ').title()} Workstream"
        if assigned_to != "unassigned":
            return f"Workstream for {assigned_to}"
        if due_bucket != "none":
            return f"{due_bucket} Delivery Goal"
        return "Inferred Execution Goal"

    async def _build_goal_inference_candidates(
        self,
        user_id: str,
        *,
        min_cluster_size: int,
        max_proposals: int,
    ) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        from graphclaw.models.enums import TaskState

        tasks = await self._fetch_active_tasks(user_id)
        if not tasks:
            return []

        clustered: dict[tuple[str, str, str], list[TaskNode]] = {}

        for task in tasks:
            if task.state in (TaskState.COMPLETE, TaskState.CANCELLED, TaskState.SNOOZED):
                continue

            try:
                parent_edges = await self._repo.get_edges(
                    task.id, direction="out", edge_type="PART_OF"
                )
            except Exception:  # noqa: BLE001
                parent_edges = []
            if parent_edges:
                continue

            assigned_to = str(task.assigned_to or "unassigned")
            due_bucket = "none"
            if task.timeline and task.timeline.deadline:
                due_bucket = task.timeline.deadline.strftime("%Y-%m")

            normalized_tags = sorted(
                {
                    str(tag).strip().lower()
                    for tag in (task.tags or [])
                    if isinstance(tag, str) and str(tag).strip()
                }
            )
            topic = (
                normalized_tags[0]
                if normalized_tags
                else str(getattr(task.task_type, "value", task.task_type or "")).lower()
            )

            key = (assigned_to, due_bucket, topic)
            clustered.setdefault(key, []).append(task)

        proposals: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for (assigned_to, due_bucket, topic), grouped_tasks in clustered.items():
            if len(grouped_tasks) < min_cluster_size:
                continue

            task_ids = [task.id for task in grouped_tasks]
            task_titles = [task.title for task in grouped_tasks][:5]

            tag_sets: list[set[str]] = []
            for task in grouped_tasks:
                tag_sets.append(
                    {
                        str(tag).strip().lower()
                        for tag in (task.tags or [])
                        if isinstance(tag, str) and str(tag).strip()
                    }
                )

            shared_tags = set.intersection(*tag_sets) if tag_sets else set()

            confidence = 0.35
            rationale: list[str] = []

            if assigned_to != "unassigned":
                confidence += 0.2
                rationale.append(f"Shared assignee: {assigned_to}")
            if due_bucket != "none":
                confidence += 0.15
                rationale.append(f"Deadline cluster: {due_bucket}")
            if shared_tags:
                confidence += min(0.25, 0.05 * len(shared_tags))
                rationale.append(f"Shared tags: {', '.join(sorted(shared_tags)[:3])}")
            confidence += min(0.1, 0.02 * len(grouped_tasks))
            confidence = max(0.0, min(confidence, 0.95))

            due_text = (
                "no shared deadline window"
                if due_bucket == "none"
                else f"a {due_bucket} deadline window"
            )
            inferred_title = self._build_goal_inference_title(
                topic=topic,
                assigned_to=assigned_to,
                due_bucket=due_bucket,
            )
            inferred_description = (
                f"Inferred from {len(grouped_tasks)} active tasks sharing "
                f"{due_text} and related ownership/context signals."
            )
            inference_note = (
                "Agent inferred this goal from bottom-up task clustering. "
                f"Signals: {', '.join(rationale) if rationale else 'task similarity'}"
            )

            proposals.append(
                {
                    "inference_id": f"GINF-{uuid.uuid4().hex[:10].upper()}",
                    "created_at": now.isoformat(),
                    "status": _GOAL_INFERENCE_STATUS_DRAFT,
                    "confidence_score": round(confidence, 3),
                    "task_ids": task_ids,
                    "task_titles": task_titles,
                    "rationale": rationale,
                    "proposal": {
                        "title": inferred_title,
                        "description": inferred_description,
                        "priority": "P2",
                        "origin": "AGENT_INFERRED",
                        "inferred_from": task_ids,
                        "inference_note": inference_note,
                        "confirmed_by_user": False,
                    },
                }
            )

        proposals.sort(
            key=lambda item: (
                float(item.get("confidence_score", 0.0)),
                len(item.get("task_ids", [])),
            ),
            reverse=True,
        )
        return proposals[:max_proposals]

    async def _tool_propose_goal_inference(
        self,
        user_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Propose bottom-up inferred goals from ungrouped task clusters."""
        try:
            min_cluster_size = int(args.get("min_cluster_size", 3) or 3)
        except (TypeError, ValueError):
            min_cluster_size = 3
        min_cluster_size = max(2, min(min_cluster_size, 10))

        try:
            max_proposals = int(args.get("max_proposals", 3) or 3)
        except (TypeError, ValueError):
            max_proposals = 3
        max_proposals = max(1, min(max_proposals, 10))

        proposals = await self._build_goal_inference_candidates(
            user_id,
            min_cluster_size=min_cluster_size,
            max_proposals=max_proposals,
        )
        if not proposals:
            return {
                "proposals": [],
                "count": 0,
                "status": "no_candidates",
                "message": "No candidate task clusters met the inference threshold.",
            }

        persisted_summaries: list[dict[str, Any]] = []
        for proposal in proposals:
            payload = {
                "inference_id": proposal["inference_id"],
                "user_id": user_id,
                "status": _GOAL_INFERENCE_STATUS_DRAFT,
                "created_at": proposal["created_at"],
                "updated_at": proposal["created_at"],
                "confidence_score": proposal["confidence_score"],
                "task_ids": proposal["task_ids"],
                "rationale": proposal["rationale"],
                "proposal": proposal["proposal"],
            }
            await self._persist_goal_inference(user_id, payload)
            persisted_summaries.append(
                {
                    "inference_id": payload["inference_id"],
                    "status": payload["status"],
                    "confidence_score": payload["confidence_score"],
                    "task_count": len(payload["task_ids"]),
                    "goal_title": payload["proposal"]["title"],
                }
            )

        return {
            "proposals": persisted_summaries,
            "count": len(persisted_summaries),
            "status": "draft — awaiting user review and approval",
        }

    async def _tool_approve_goal_inference(
        self,
        user_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Approve and commit a draft goal-inference proposal to the graph."""
        import datetime as _dt

        from graphclaw.models.base import generate_id
        from graphclaw.models.enums import GoalOrigin, GoalPriority, GoalState
        from graphclaw.models.nodes import GoalNode

        inference_id = str(args.get("inference_id", "")).strip()
        if not inference_id:
            return {"error": "inference_id is required."}

        inference = await self._load_goal_inference(user_id, inference_id)
        if not inference:
            return {"error": f"Goal inference {inference_id} not found."}

        if str(
            inference.get("status", "")
        ).upper() == _GOAL_INFERENCE_STATUS_APPROVED and inference.get("goal_id"):
            return {
                "inference_id": inference_id,
                "status": _GOAL_INFERENCE_STATUS_APPROVED,
                "goal_id": inference.get("goal_id"),
                "message": "Goal inference is already approved and committed.",
            }

        proposal = inference.get("proposal") or {}
        if not isinstance(proposal, dict):
            return {"error": f"Goal inference {inference_id} is malformed."}

        task_ids_raw = inference.get("task_ids") or proposal.get("inferred_from") or []
        task_ids = [str(task_id).strip() for task_id in task_ids_raw if str(task_id).strip()]
        if not task_ids:
            return {"error": "Goal inference has no linked task_ids to commit."}

        goal_id = generate_id("GOAL")
        now = _dt.datetime.now(_dt.timezone.utc)

        try:
            priority_raw = str(proposal.get("priority", "P2"))
            priority = GoalPriority(priority_raw)
        except ValueError:
            priority = GoalPriority.P2

        goal = GoalNode(
            id=goal_id,
            title=str(proposal.get("title") or "Inferred Goal"),
            description=str(proposal.get("description") or ""),
            owner=user_id,
            state=GoalState.ACTIVE,
            priority=priority,
            origin=GoalOrigin.AGENT_INFERRED,
            inferred_from=task_ids,
            inference_note=str(
                proposal.get("inference_note") or "Goal inferred from related active task cluster."
            ),
            confirmed_by_user=True,
            created_at=now,
            updated_at=now,
        )

        linked_task_ids: list[str] = []
        skipped_task_ids: list[str] = []
        try:
            await self._repo.create_node(goal)
            for task_id in task_ids:
                task_node = await self._repo.get_node(task_id)
                if not task_node:
                    skipped_task_ids.append(task_id)
                    continue
                existing_parent = await self._repo.get_edges(
                    task_id, direction="out", edge_type="PART_OF"
                )
                if existing_parent:
                    skipped_task_ids.append(task_id)
                    continue
                await self._repo.create_edge(task_id, goal_id, "PART_OF", {})
                linked_task_ids.append(task_id)
        except Exception as exc:  # noqa: BLE001
            try:
                await self._repo.delete_node(goal_id)
            except Exception:  # noqa: BLE001
                pass
            return {
                "error": f"Failed to commit goal inference {inference_id}: {exc}",
                "inference_id": inference_id,
                "rolled_back": True,
            }

        inference["status"] = _GOAL_INFERENCE_STATUS_APPROVED
        inference["goal_id"] = goal_id
        inference["approved_by"] = user_id
        inference["approved_at"] = now.isoformat()
        inference["updated_at"] = now.isoformat()
        inference["linked_task_ids"] = linked_task_ids
        inference["skipped_task_ids"] = skipped_task_ids
        await self._persist_goal_inference(user_id, inference)

        self._invalidate_cached_queue(user_id, dirty_task_ids=set(linked_task_ids))

        return {
            "inference_id": inference_id,
            "goal_id": goal_id,
            "status": _GOAL_INFERENCE_STATUS_APPROVED,
            "linked_task_count": len(linked_task_ids),
            "skipped_task_count": len(skipped_task_ids),
            "message": "Goal inference approved and committed to graph.",
        }

    async def _tool_propose_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Use an inner LLM call to decompose a goal into a structured plan."""
        if self._llm is None:
            return {"error": "LLM not configured — cannot generate plans."}

        from graphclaw.llm.base import LLMMessage

        description = str(args.get("description", "")).strip()
        context = str(args.get("context", "")).strip()
        goal_or_task_id = str(args.get("goal_or_task_id", "")).strip()
        constraints = str(args.get("constraints", "")).strip()
        if not constraints and context and context != description:
            constraints = context
        deadline = str(args.get("deadline", "")).strip()
        try:
            max_tasks = int(args.get("max_tasks", 10) or 10)
        except (TypeError, ValueError):
            max_tasks = 10
        max_tasks = max(1, min(max_tasks, 50))

        # Backward-compatible arg resolution: derive a description from target node when needed.
        if not description and goal_or_task_id:
            target = await self._repo.get_node(goal_or_task_id)
            if target:
                node_title = str(target.get("title", goal_or_task_id))
                node_desc = str(target.get("description", "")).strip()
                description = (
                    f"Create an execution plan for {goal_or_task_id}: {node_title}."
                    f"\n\nExisting description:\n{node_desc}"
                    if node_desc
                    else f"Create an execution plan for {goal_or_task_id}: {node_title}."
                )

        if not description:
            return {
                "error": (
                    "description is required (or provide goal_or_task_id/context so a plan target can be inferred)."
                )
            }

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
            f"- Return at most {max_tasks} tasks\n\n"
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

        response = await self._llm.complete(messages, model=None, max_tokens=4096, temperature=0.3)
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

        tasks = plan_data.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        tasks = tasks[:max_tasks]
        for idx, task in enumerate(tasks):
            if isinstance(task, dict):
                task.setdefault("draft_task_id", f"DRAFT-TASK-{idx + 1}")
        plan_data["tasks"] = tasks

        # Generate plan_id and store for review/execution lifecycle
        import datetime as _dt

        plan_id = f"PLAN-{uuid.uuid4().hex[:12]}"
        plan_data["plan_id"] = plan_id
        plan_data["user_id"] = user_id
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        plan_data["status"] = _PLAN_STATUS_DRAFT
        plan_data["revision"] = 1
        plan_data["created_at"] = now
        plan_data["updated_at"] = now
        if deadline:
            plan_data["deadline"] = deadline

        await self._persist_pending_plan(user_id, plan_data)

        return {
            "plan_id": plan_id,
            "goal_title": plan_data.get("goal_title", ""),
            "tasks": plan_data.get("tasks", []),
            "execution_summary": plan_data.get("execution_summary", ""),
            "task_count": len(plan_data.get("tasks", [])),
            "status": "draft — awaiting user review and approval",
            "revision": plan_data.get("revision", 1),
        }

    async def _tool_edit_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Apply human-reviewed edits to a draft plan before approval."""
        plan_id = str(args.get("plan_id", "")).strip()
        if not plan_id:
            return {"error": "plan_id is required."}

        plan_data = await self._load_pending_plan(user_id, plan_id)
        if not plan_data:
            return {"error": f"Plan {plan_id} not found. Call propose_plan first."}

        if plan_data.get("status") == _PLAN_STATUS_EXECUTED:
            return {"error": f"Plan {plan_id} has already been executed and cannot be edited."}

        updated = False
        for field in ("goal_title", "goal_description", "execution_summary", "deadline"):
            if field in args:
                plan_data[field] = args.get(field)
                updated = True

        if "tasks" in args:
            tasks = args.get("tasks")
            if not isinstance(tasks, list):
                return {"error": "tasks must be an array when provided."}
            normalized: list[dict[str, Any]] = []
            for idx, task in enumerate(tasks):
                if not isinstance(task, dict):
                    return {"error": f"tasks[{idx}] must be an object."}
                task_copy = dict(task)
                task_copy.setdefault("draft_task_id", f"DRAFT-TASK-{idx + 1}")
                normalized.append(task_copy)
            plan_data["tasks"] = normalized
            updated = True

        if not updated:
            return {
                "plan_id": plan_id,
                "status": plan_data.get("status", _PLAN_STATUS_DRAFT),
                "task_count": len(plan_data.get("tasks", [])),
                "message": "No changes provided.",
            }

        previous_status = str(plan_data.get("status", _PLAN_STATUS_DRAFT))
        if previous_status == _PLAN_STATUS_APPROVED:
            # Any edit after approval requires re-approval before execution.
            plan_data["status"] = _PLAN_STATUS_DRAFT
            plan_data.pop("approved_at", None)
            plan_data.pop("approved_by", None)

        import datetime as _dt

        plan_data["revision"] = int(plan_data.get("revision", 1)) + 1
        plan_data["updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        await self._persist_pending_plan(user_id, plan_data)

        return {
            "plan_id": plan_id,
            "status": plan_data.get("status", _PLAN_STATUS_DRAFT),
            "revision": plan_data.get("revision", 1),
            "task_count": len(plan_data.get("tasks", [])),
            "message": "Plan updated.",
        }

    async def _tool_approve_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Mark a reviewed draft plan as approved for execution."""
        import datetime as _dt

        plan_id = str(args.get("plan_id", "")).strip()
        if not plan_id:
            return {"error": "plan_id is required."}

        plan_data = await self._load_pending_plan(user_id, plan_id)
        if not plan_data:
            return {"error": f"Plan {plan_id} not found. Call propose_plan first."}

        status = str(plan_data.get("status", ""))
        if status == _PLAN_STATUS_EXECUTED:
            return {"error": f"Plan {plan_id} has already been executed."}
        if status == _PLAN_STATUS_APPROVED:
            return {
                "plan_id": plan_id,
                "status": _PLAN_STATUS_APPROVED,
                "message": "Plan is already approved.",
            }

        plan_data["status"] = _PLAN_STATUS_APPROVED
        plan_data["approved_by"] = user_id
        plan_data["approved_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        plan_data["updated_at"] = plan_data["approved_at"]
        await self._persist_pending_plan(user_id, plan_data)

        return {
            "plan_id": plan_id,
            "status": _PLAN_STATUS_APPROVED,
            "task_count": len(plan_data.get("tasks", [])),
            "message": "Plan approved. You can now call execute_plan.",
        }

    async def _tool_execute_plan(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Create all tasks from an approved plan in the graph."""
        plan_id = args["plan_id"]

        # Load plan from memory cache or storage.
        plan_data = await self._load_pending_plan(user_id, plan_id)

        if not plan_data:
            return {"error": f"Plan {plan_id} not found. Call propose_plan first."}
        if plan_data.get("status") == _PLAN_STATUS_EXECUTED:
            return {"error": f"Plan {plan_id} has already been executed."}
        if plan_data.get("status") != _PLAN_STATUS_APPROVED:
            return {
                "error": (
                    f"Plan {plan_id} is {plan_data.get('status', 'UNKNOWN')}. "
                    "Call approve_plan before execute_plan."
                )
            }

        approved_task_ids_arg = args.get("approved_task_ids") or []
        approved_task_ids = {
            str(task_id).strip()
            for task_id in approved_task_ids_arg
            if isinstance(task_id, str) and str(task_id).strip()
        }

        # Create the goal/tasks atomically using compensating rollback when any step fails.
        created_node_ids: list[str] = []
        created_tasks: list[dict[str, Any]] = []
        index_to_task_id: dict[int, str] = {}

        try:
            goal_result = await self._tool_create_goal(
                user_id,
                {
                    "title": plan_data.get("goal_title", "Untitled Goal"),
                    "description": plan_data.get("goal_description", ""),
                    "deadline": plan_data.get("deadline", ""),
                },
            )
            if "error" in goal_result:
                raise RuntimeError(str(goal_result["error"]))
            goal_id = str(goal_result.get("goal_id", "")).strip()
            if not goal_id:
                raise RuntimeError("Goal creation did not return a goal_id.")
            created_node_ids.append(goal_id)

            # Create tasks and track created IDs for dependency wiring
            tasks = plan_data.get("tasks", [])
            if not isinstance(tasks, list):
                tasks = []

            indexed_tasks: list[tuple[int, dict[str, Any]]] = []
            for original_idx, task in enumerate(tasks):
                if not isinstance(task, dict):
                    continue
                if (
                    approved_task_ids
                    and str(task.get("draft_task_id", "")).strip() not in approved_task_ids
                ):
                    continue
                indexed_tasks.append((original_idx, task))

            for original_idx, task_spec in indexed_tasks:
                if not isinstance(task_spec, dict):
                    continue

                # Resolve depends_on from indices to task IDs
                depends_on_ids = []
                for dep_idx in task_spec.get("depends_on_indices", []):
                    dep_task_id = index_to_task_id.get(dep_idx)
                    if dep_task_id:
                        depends_on_ids.append(dep_task_id)

                task_result = await self._tool_create_task(
                    user_id,
                    {
                        "title": task_spec.get("title", f"Task {original_idx + 1}"),
                        "description": task_spec.get("description", ""),
                        "task_type": task_spec.get("task_type", "atomic"),
                        "goal_id": goal_id,
                        "depends_on": depends_on_ids,
                    },
                )
                if "error" in task_result:
                    raise RuntimeError(str(task_result["error"]))

                task_id = str(task_result.get("task_id", "")).strip()
                if not task_id:
                    raise RuntimeError(
                        f"Plan task at index {original_idx} was created without a task_id."
                    )

                index_to_task_id[original_idx] = task_id
                created_node_ids.append(task_id)

                followup_task_id = str(task_result.get("follow_up_task_id", "")).strip()
                if followup_task_id:
                    created_node_ids.append(followup_task_id)

                created_tasks.append(
                    {
                        "task_id": task_id,
                        "title": task_spec.get("title", ""),
                        "task_type": task_spec.get("task_type", "atomic"),
                        "can_be_automated": task_spec.get("can_be_automated", False),
                        "assigned_skill": task_spec.get("assigned_skill"),
                        "assigned_mcp_server": task_spec.get("assigned_mcp_server"),
                        "assigned_mcp_tool": task_spec.get("assigned_mcp_tool"),
                        "draft_task_id": task_spec.get("draft_task_id"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            for node_id in reversed(created_node_ids):
                try:
                    await self._repo.delete_node(node_id)
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(
                        "AgentLoop: rollback failed for node %s during execute_plan: %s",
                        node_id,
                        rollback_exc,
                    )
            return {
                "error": f"Execution failed for plan {plan_id}: {exc}",
                "plan_id": plan_id,
                "rolled_back": True,
                "status": "failed",
            }

        # Mark plan as executed
        import datetime as _dt

        plan_data["status"] = _PLAN_STATUS_EXECUTED
        plan_data["executed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        plan_data["updated_at"] = plan_data["executed_at"]
        await self._persist_pending_plan(user_id, plan_data)

        return {
            "plan_id": plan_id,
            "goal_id": goal_id,
            "created_tasks": created_tasks,
            "total_created": len(created_tasks),
            "status": _PLAN_STATUS_EXECUTED,
        }

    # ------------------------------------------------------------------
    # Skill dispatch tools
    # ------------------------------------------------------------------

    async def _load_agent_config(self, user_id: str, agent_id: str) -> dict[str, Any] | None:
        """Load runtime config.json for an agent from storage.  Returns None on miss."""
        if self._storage is None:
            return None
        from graphclaw.infra.storage import StoragePaths

        try:
            raw = await self._storage.read(StoragePaths.agent_config(user_id, agent_id))
            return json.loads(raw.decode())
        except (FileNotFoundError, Exception):
            return None

    async def _tool_list_available_skills(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """List skills available to the user, filtered by agent config if scoped."""
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
            # C2: filter by agent config.json.skills[] if present
            agent_cfg = await self._load_agent_config(user_id, self._agent_id)
            allowed = agent_cfg.get("skills") if agent_cfg else None
            if isinstance(allowed, list):
                skills = [s for s in skills if s["skill_id"] in allowed]
            return {"skills": skills, "count": len(skills)}
        except Exception as exc:
            logger.warning("AgentLoop: list_available_skills failed: %s", exc)
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
                await self._invalidate_score_cache_for_task(task_id, include_related=False)
                self._invalidate_cached_queue(user_id, dirty_task_ids={task_id})
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
        """List MCP servers and their tools for the user, filtered by agent config if scoped."""
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

        # C3: filter by agent config.json.mcp_servers[] if present
        agent_cfg = await self._load_agent_config(user_id, self._agent_id)
        allowed_mcp = agent_cfg.get("mcp_servers") if agent_cfg else None
        if isinstance(allowed_mcp, list):
            servers = [s for s in servers if s.id in allowed_mcp]

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
            logger.info(
                "mcp.tool_call",
                extra={
                    "event_type": "mcp.tool_call",
                    "session_id": self._current_session_id or "",
                    "user_id": user_id,
                    "server_id": server_id,
                    "server_name": server.name,
                    "tool_name": tool_name,
                    "success": success,
                    "latency_ms": latency_ms,
                },
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
                user_id=user_id,
                agent_source="user",  # batch dispatch stubs default to user; resolved later
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

        # C5: validate delegation target is in config.json.sub_agents[] if scoped
        agent_cfg = await self._load_agent_config(user_id, self._agent_id)
        if agent_cfg is not None:
            allowed_sub = agent_cfg.get("sub_agents")
            if isinstance(allowed_sub, list) and agent_id not in allowed_sub:
                return {
                    "error": (
                        f"Agent '{agent_id}' is not in the allowed sub_agents list for this "
                        "orchestrator. Use list_available_agents to see permitted targets."
                    )
                }

        # Verify the task exists
        task_props = await self._repo.get_node(task_id)
        if not task_props:
            return {"error": f"Task {task_id} not found."}

        # Resolve whether this is a system or user agent
        agent_source = "user"
        if self._agent_catalog is not None:
            agent_source = await self._agent_catalog.resolve_source(user_id, agent_id)
        elif self._storage:
            # Fallback: check system manifest directly
            from graphclaw.infra.storage import StoragePaths

            try:
                await self._storage.read(StoragePaths.system_agent_manifest(agent_id))
                agent_source = "system"
            except FileNotFoundError:
                agent_source = "user"
            except Exception:
                pass

        # Validate the resolved agent source against manifest existence.
        if self._storage:
            from graphclaw.infra.storage import StoragePaths

            manifest_path = (
                StoragePaths.system_agent_manifest(agent_id)
                if agent_source == "system"
                else StoragePaths.agent_manifest(user_id, agent_id)
            )
            try:
                await self._storage.read(manifest_path)
            except FileNotFoundError:
                return {
                    "error": (
                        f"Agent '{agent_id}' not found for user '{user_id}'. "
                        "Call list_available_agents first."
                    )
                }
            except Exception as exc:
                logger.warning("AgentLoop: agent manifest lookup failed for %s: %s", agent_id, exc)
                return {"error": f"Could not validate agent '{agent_id}': {exc}"}

        # Update task state to IN_PROGRESS and assign to agent
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        previous_assignee = task_props.get("assigned_to")
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

        # Record ownership transition context as a HandoffNode when assignee changes.
        if previous_assignee != agent_id:
            try:
                from graphclaw.models.base import generate_handoff_node_id
                from graphclaw.models.nodes import HandoffNode

                handoff_id = generate_handoff_node_id()
                handoff = HandoffNode(
                    id=handoff_id,
                    created_at=now,
                    updated_at=now,
                    task_id=task_id,
                    from_owner=previous_assignee,
                    to_owner=agent_id,
                    context_summary=(instructions or "Delegation handoff").strip(),
                    context_refs=[],
                    transitioned_at=now,
                )
                await self._repo.create_node(handoff)
                await self._repo.create_edge(handoff_id, task_id, "REFERRED_BY")
            except Exception as exc:
                logger.warning(
                    "AgentLoop: non-fatal handoff persistence failure for task %s: %s",
                    task_id,
                    exc,
                )

        await self._invalidate_score_cache_for_task(task_id, include_related=False)
        self._invalidate_cached_queue(user_id, dirty_task_ids={task_id})

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
                user_id=user_id,
                agent_source=agent_source,
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

        # Accept both the current schema (name/purpose/skills) and legacy
        # variants (agent_id/profile/capabilities) for compatibility.
        name = str(args.get("name") or args.get("agent_id") or "").strip()
        if not name:
            return {"error": "name is required"}

        purpose = str(args.get("purpose") or args.get("profile") or "").strip()
        if not purpose:
            purpose = "User-created sub-agent"

        skills = args.get("skills") or args.get("capabilities") or []
        if not isinstance(skills, list):
            return {"error": "skills/capabilities must be a list"}

        mcp_servers = args.get("mcp_servers", [])
        if not isinstance(mcp_servers, list):
            return {"error": "mcp_servers must be a list"}

        requested_agent_id = str(args.get("agent_id") or "").strip()

        def _slugify(value: str) -> str:
            raw = value.lower().replace("_", "-").replace(" ", "-")
            cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == "-").strip("-")
            return cleaned or "agent"

        # Generate/normalise agent_id — always deterministic from name, never UUID-suffixed.
        # This guarantees idempotency: creating "Research Agent" twice gives the same agent_id.
        agent_id = _slugify(requested_agent_id or name)[:40]

        profile_path = StoragePaths.agent_profile(user_id, agent_id)
        manifest_path = StoragePaths.agent_manifest(user_id, agent_id)
        config_path = StoragePaths.agent_config(user_id, agent_id)
        context_path = StoragePaths.agent_memory_working(user_id, agent_id)

        # Idempotency check — return error if agent already exists.
        try:
            if await self._storage.exists(profile_path) or await self._storage.exists(
                manifest_path
            ):
                return {"error": f"Agent '{agent_id}' already exists."}
        except Exception:
            pass

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

        manifest = {
            "agent_id": agent_id,
            "name": name,
            "type": "user",
            "description": purpose,
            "capabilities": skills,
            "invocation": "async",
            "tool_hint": args.get("tool_hint") or f"{purpose}",
        }

        knowledge_path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, "knowledge")
        try:
            await self._storage.write(profile_path, profile_content.encode())
            await self._storage.write(
                manifest_path,
                json.dumps(manifest, indent=2).encode(),
                content_type="application/json",
            )
            await self._storage.write(config_path, json.dumps(config, indent=2).encode())
            await self._storage.write(
                context_path, b"# Working Context\n\nAgent initialised. Awaiting first task.\n"
            )
            await self._storage.write(
                knowledge_path,
                f"# Knowledge: {name}\n\nAdd agent-specific facts and knowledge here.\n".encode(),
                content_type="text/markdown",
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
            "manifest_path": manifest_path,
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

    async def _fetch_active_tasks(self, user_id: str | None = None) -> list[TaskNode]:
        """Retrieve non-terminal TaskNode records, scoped to *user_id* when provided.

        Parameters
        ----------
        user_id:
            When given, only tasks with ``owned_by == user_id`` are returned.
            When ``None``, all tasks are returned (used for internal scoring cycles
            where user context is not yet available).
        """
        from graphclaw.models.enums import TaskState

        _TERMINAL = {
            TaskState.COMPLETE.value,
            TaskState.CANCELLED.value,
            TaskState.SNOOZED.value,
        }

        try:
            if user_id:
                raw_nodes = await self._repo.list_nodes_by_user("TaskNode", user_id)
            else:
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

    # ------------------------------------------------------------------
    # Identity tool handlers (FR-ID-001..005)
    # ------------------------------------------------------------------

    async def _tool_resolve_user(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.identity.resolver import UserResolver  # noqa: PLC0415

        query = args.get("query", "")
        hints = args.get("hints") or {}
        resolver = UserResolver(self._store)
        candidates = await resolver.resolve(query, user_id, [], hints)
        return {
            "candidates": [
                {
                    "node_id": c.node_id,
                    "source": c.source,
                    "confidence": c.confidence,
                    "display_name": c.display_name,
                    "reason": c.reason,
                }
                for c in candidates
            ]
        }

    async def _tool_start_create_person_dialog(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        from graphclaw.agent.tools.identity_tools import start_create_person_dialog  # noqa: PLC0415

        query = args.get("query", "")
        session_ctx = self._session_context if hasattr(self, "_session_context") else {}
        return await start_create_person_dialog(
            query=query,
            caller_user_id=user_id,
            store=self._store,
            session_context=session_ctx,
        )

    async def _tool_respond_to_create_person_dialog(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        from graphclaw.agent.tools.identity_tools import (
            respond_to_create_person_dialog,  # noqa: PLC0415
        )

        session_ctx = self._session_context if hasattr(self, "_session_context") else {}
        return await respond_to_create_person_dialog(
            session_key=args.get("session_key", ""),
            user_input=args.get("user_input", ""),
            store=self._store,
            session_context=session_ctx,
        )

    async def _tool_merge_resource(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.identity_tools import merge_resource  # noqa: PLC0415

        return await merge_resource(
            keep_id=args.get("keep_id", ""),
            merge_id=args.get("merge_id", ""),
            canonical_name=args.get("canonical_name"),
            store=self._store,
            storage=self._storage,
            broker=getattr(self, "_broker", None),
        )

    async def _tool_register_alias(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.identity_tools import register_alias  # noqa: PLC0415

        return await register_alias(
            node_id=args.get("node_id", ""),
            alias=args.get("alias", ""),
            source=args.get("source", "user"),
            added_by=user_id,
            store=self._store,
        )

    async def _tool_set_user_name(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import set_user_name  # noqa: PLC0415

        return await set_user_name(user_id=user_id, name=args.get("name", ""), store=self._store)

    async def _tool_set_user_persona(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import set_user_persona  # noqa: PLC0415

        return await set_user_persona(
            user_id=user_id,
            role=args.get("role", ""),
            timezone=args.get("timezone", "UTC"),
            store=self._store,
        )

    async def _tool_add_user_identity(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import add_user_identity  # noqa: PLC0415

        return await add_user_identity(
            user_id=user_id,
            channel=args.get("channel", ""),
            value=args.get("value", ""),
            store=self._store,
        )

    async def _tool_set_working_hours(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import set_working_hours  # noqa: PLC0415

        return await set_working_hours(
            user_id=user_id,
            start=args.get("start", "09:00"),
            end=args.get("end", "18:00"),
            store=self._store,
        )

    async def _tool_set_preferences(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import set_preferences  # noqa: PLC0415

        return await set_preferences(
            user_id=user_id,
            preferred_channel=args.get("preferred_channel", "email"),
            briefing_time=args.get("briefing_time", "08:00"),
            briefing_style=args.get("briefing_style", "summary"),
            default_follow_up_days=int(args.get("default_follow_up_days", 3)),
            store=self._store,
        )

    async def _tool_seed_policy_from_template(
        self, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import (
            seed_policy_from_template,  # noqa: PLC0415
        )

        return await seed_policy_from_template(
            user_id=user_id,
            agent_id=self._agent_id,
            policy_name=args.get("policy_name", "delegation"),
            storage=self._storage,
        )

    async def _tool_complete_onboarding(self, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        from graphclaw.agent.tools.onboarding_tools import complete_onboarding  # noqa: PLC0415

        return await complete_onboarding(
            user_id=user_id,
            agent_id=self._agent_id,
            storage=self._storage,
        )


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


# Backward-compatibility alias while import sites migrate.
AgentLoop = MainOrchestrator

__all__ = ["MainOrchestrator", "AgentLoop"]
