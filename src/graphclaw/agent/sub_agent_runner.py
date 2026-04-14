"""graphclaw.agent.sub_agent_runner — SubAgentRunner: executes delegated tasks autonomously.

Description
-----------
``SubAgentRunner`` is a lightweight, long-running async executor for tasks delegated
by the main ``AgentLoop``.  Each runner reads the delegation context written by
``_tool_delegate_to_agent()`` from MinIO, calls the LLM in a tool-use loop (up to
15 iterations), and emits typed events to the ``AGENT_UPDATES`` broker queue so the
orchestrator and ``AgentEventConsumer`` can track progress.

Key design constraints (Phase 5):
- Flat delegation only — runner toolset excludes ``delegate_to_agent``.
- Uses a dedicated ``WorkerPool`` (separate from orchestrator pool).
- On heartbeat timeout: caller marks task BLOCKED; runner itself only emits.
- All events carry ``agent_id + task_id`` for audit correlation.

Design Patterns
---------------
- State Machine: IDLE → RUNNING → COMPLETED / FAILED / TIMED_OUT.
- Dependency Injection: All collaborators injected at construction time.
- Structured Events: Typed broker events replace generic inbound parsing.

Public API
----------
- SubAgentRunner: Single async delegated task executor.
- SubAgentRunner.execute: Run one AgentJobEvent and publish results to AGENT_UPDATES.
- SubAgentRunner.status: Property returning a RunnerStatus snapshot.
- RunnerState: Enum of valid runner states.
- RunnerStatus: Point-in-time status snapshot model.
- AgentJobEvent: Pydantic model for AGENT_JOBS queue payloads.
- AgentUpdateEvent / AgentUpdateEventType: Pydantic model for AGENT_UPDATES payloads.

Dependencies
------------
- asyncio: task lifecycle.
- graphclaw.infra.broker: MessageBroker, AGENT_UPDATES.
- graphclaw.infra.logger: AsyncLogger, audit event classes.
- graphclaw.infra.storage: StorageClient, StoragePaths.
- graphclaw.llm.base: LLMClient (TYPE_CHECKING).
- graphclaw.skills.worker: WorkerPool (TYPE_CHECKING).
- graphclaw.mcp.registry: MCPRegistry (TYPE_CHECKING).
- graphclaw.skills.registry: SkillRegistryService (TYPE_CHECKING).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from graphclaw.infra.broker import AGENT_UPDATES, MessageBroker
from graphclaw.models.base import utcnow

if TYPE_CHECKING:
    from graphclaw.infra.logger import AsyncLogger
    from graphclaw.infra.storage import StorageClient
    from graphclaw.llm.base import LLMClient
    from graphclaw.mcp.registry import MCPRegistry
    from graphclaw.skills.registry import SkillRegistryService
    from graphclaw.skills.worker import WorkerPool

logger = logging.getLogger(__name__)

# Maximum LLM tool-use iterations per delegation (mirrors AgentLoop)
_MAX_ITERATIONS = 15


# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------


class AgentJobEvent(BaseModel):
    """Payload published to the ``AGENT_JOBS`` queue by ``_tool_delegate_to_agent``."""

    agent_id: str
    task_id: str
    session_id: str
    parent_task_id: str | None = None
    batch_id: str = Field(default_factory=lambda: f"batch-{uuid.uuid4().hex[:8]}")
    instructions: str = ""
    dispatched_at: datetime = Field(default_factory=utcnow)


class AgentUpdateEventType(str, Enum):
    """Types of events emitted to the ``AGENT_UPDATES`` queue."""

    STARTED = "started"
    PROGRESS = "progress"
    HEARTBEAT = "heartbeat"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class AgentUpdateEvent(BaseModel):
    """Payload published to the ``AGENT_UPDATES`` queue by ``SubAgentRunner``."""

    event_type: AgentUpdateEventType
    agent_id: str
    task_id: str
    session_id: str
    parent_task_id: str | None = None
    batch_id: str = ""
    message: str | None = None
    status: str | None = None  # COMPLETED | FAILED | TIMED_OUT (completed only)
    duration_ms: int | None = None  # completed events only
    emitted_at: datetime = Field(default_factory=utcnow)


class RunnerState(str, Enum):
    """Lifecycle states for a SubAgentRunner."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class RunnerStatus(BaseModel):
    """Point-in-time snapshot of a SubAgentRunner."""

    runner_id: str
    state: RunnerState
    agent_id: str | None
    task_id: str | None
    session_id: str | None
    batch_id: str | None
    started_at: datetime | None
    last_heartbeat: datetime | None
    elapsed_ms: int | None


# ---------------------------------------------------------------------------
# SubAgentRunner
# ---------------------------------------------------------------------------


class SubAgentRunner:
    """Executes a single delegated task using an LLM tool-use loop.

    Parameters
    ----------
    runner_id:
        Unique identifier for this runner instance (e.g. ``"runner-000"``).
    broker:
        MessageBroker for publishing events to AGENT_UPDATES.
    llm_client:
        LLMClient for language model calls.
    storage:
        StorageClient for reading agent profiles and delegation context.
    worker_pool:
        Dedicated WorkerPool for skill execution (sub-agent pool, separate
        from the orchestrator pool).
    skill_registry:
        Optional SkillRegistryService for resolving skill definitions.
    mcp_registry:
        Optional MCPRegistry for MCP tool calls.
    async_logger:
        Optional AsyncLogger for structured audit events.
    heartbeat_interval:
        Seconds between heartbeat event emissions (default 60).
    """

    def __init__(
        self,
        runner_id: str,
        broker: MessageBroker,
        llm_client: LLMClient,
        storage: StorageClient | None = None,
        worker_pool: WorkerPool | None = None,
        skill_registry: SkillRegistryService | None = None,
        mcp_registry: MCPRegistry | None = None,
        async_logger: AsyncLogger | None = None,
        heartbeat_interval: int = 60,
    ) -> None:
        self._runner_id = runner_id
        self._broker = broker
        self._llm = llm_client
        self._storage = storage
        self._worker_pool = worker_pool
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._logger = async_logger
        self._heartbeat_interval = heartbeat_interval

        self._state: RunnerState = RunnerState.IDLE
        self._current_job: AgentJobEvent | None = None
        self._started_at: datetime | None = None
        self._last_heartbeat: datetime | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> RunnerState:
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state in (
            RunnerState.IDLE,
            RunnerState.COMPLETED,
            RunnerState.FAILED,
            RunnerState.TIMED_OUT,
        )

    @property
    def status(self) -> RunnerStatus:
        elapsed = None
        if self._started_at is not None:
            elapsed = int((utcnow() - self._started_at).total_seconds() * 1000)
        return RunnerStatus(
            runner_id=self._runner_id,
            state=self._state,
            agent_id=self._current_job.agent_id if self._current_job else None,
            task_id=self._current_job.task_id if self._current_job else None,
            session_id=self._current_job.session_id if self._current_job else None,
            batch_id=self._current_job.batch_id if self._current_job else None,
            started_at=self._started_at,
            last_heartbeat=self._last_heartbeat,
            elapsed_ms=elapsed,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, job: AgentJobEvent) -> str:
        """Execute a delegated task job.

        Reads agent profile + delegation context from storage, calls the
        LLM in a tool-use loop, and emits structured events to AGENT_UPDATES.

        Args:
            job: The ``AgentJobEvent`` describing what to execute.

        Returns:
            Final status string: ``"COMPLETED"``, ``"FAILED"``, or ``"TIMED_OUT"``.
        """
        self._state = RunnerState.RUNNING
        self._current_job = job
        self._started_at = utcnow()
        self._last_heartbeat = utcnow()
        start_ts = time.monotonic()

        await self._emit(AgentUpdateEventType.STARTED, job, message=f"Starting task {job.task_id}")
        self._audit_started(job)

        # Start heartbeat background task
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job))

        final_status = "COMPLETED"
        try:
            await self._run_llm_loop(job)
            self._state = RunnerState.COMPLETED
        except asyncio.CancelledError:
            final_status = "TIMED_OUT"
            self._state = RunnerState.TIMED_OUT
            raise
        except Exception as exc:
            final_status = "FAILED"
            self._state = RunnerState.FAILED
            logger.exception(
                "SubAgentRunner %s failed on task %s: %s", self._runner_id, job.task_id, exc
            )
            await self._emit(
                AgentUpdateEventType.BLOCKED,
                job,
                message=f"Runner failed: {exc!s}",
            )
            self._audit_blocked(job, reason=str(exc))
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            await self._emit(
                AgentUpdateEventType.COMPLETED,
                job,
                message=f"Task {job.task_id} finished with status {final_status}",
                status=final_status,
                duration_ms=duration_ms,
            )
            self._audit_completed(job, status=final_status, duration_ms=duration_ms)
            self._current_job = None

        return final_status

    # ------------------------------------------------------------------
    # LLM tool-use loop
    # ------------------------------------------------------------------

    async def _run_llm_loop(self, job: AgentJobEvent) -> None:
        """Run a multi-turn LLM tool-use loop for the delegated task."""
        from graphclaw.llm.base import LLMMessage  # local import

        system_prompt = await self._build_system_prompt(job)
        messages: list[LLMMessage] = [
            LLMMessage(
                role="user",
                content=(
                    f"You have been delegated the following task:\n\n"
                    f"Task ID: {job.task_id}\n"
                    f"Instructions: {job.instructions}\n\n"
                    f"Use the available tools to complete this task. "
                    f"Report your findings and actions clearly."
                ),
            )
        ]
        tools = self._build_tools()

        for iteration in range(_MAX_ITERATIONS):
            response = await self._llm.complete(
                messages=messages,
                system=system_prompt,
                tools=tools,
                max_tokens=4096,
            )

            # Emit progress on each iteration
            content_text = response.get("content", "")
            if content_text:
                await self._emit(
                    AgentUpdateEventType.PROGRESS,
                    job,
                    message=content_text[:200],
                )
                self._audit_progress(job, message=content_text[:200], iteration=iteration)

            tool_calls = response.get("tool_use", [])
            if not tool_calls:
                # No more tool calls — done
                break

            # Execute each tool call and append results
            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("name", "")
                tool_input = tool_call.get("input", {})
                tool_id = tool_call.get("id", f"tool-{uuid.uuid4().hex[:8]}")
                result = await self._dispatch_tool(tool_name, tool_input, job)
                tool_results.append(
                    LLMMessage(
                        role="tool",
                        content=json.dumps(result),
                        tool_use_id=tool_id,
                    )
                )

            # Append assistant turn + tool results to conversation
            messages.append(
                LLMMessage(
                    role="assistant", content=response.get("content", ""), tool_use=tool_calls
                )
            )
            messages.extend(tool_results)

    async def _build_system_prompt(self, job: AgentJobEvent) -> str:
        """Load agent profile from storage and build the system prompt."""
        base = (
            f"You are an AI sub-agent with ID '{job.agent_id}' executing a delegated task.\n"
            f"Task ID: {job.task_id}\n"
            f"Session ID: {job.session_id}\n\n"
            f"You have access to skills and MCP tools. Use them to complete your assigned task.\n"
            f"Be concise and focused. Report your actions and findings clearly.\n"
            f"You cannot delegate further — complete the task directly using available tools.\n"
        )
        if self._storage:
            try:
                from graphclaw.infra.storage import StoragePaths

                user_id = job.session_id.split("-")[1] if "-" in job.session_id else ""
                profile_path = StoragePaths.agent_profile(user_id, job.agent_id)
                profile_bytes = await self._storage.read(profile_path)
                if profile_bytes:
                    base += f"\n## Agent Profile\n{profile_bytes.decode(errors='replace')}\n"
                context_path = StoragePaths.agent_memory_working(user_id, job.agent_id)
                ctx_bytes = await self._storage.read(context_path)
                if ctx_bytes:
                    base += f"\n## Delegation Context\n{ctx_bytes.decode(errors='replace')}\n"
            except Exception as exc:
                logger.debug("SubAgentRunner: could not load profile/context: %s", exc)
        return base

    def _build_tools(self) -> list[dict[str, Any]]:
        """Return the tool definitions available to sub-agents.

        Sub-agents can only invoke skills and MCP tools (no delegation).
        """
        return [
            {
                "name": "invoke_skill",
                "description": "Invoke a skill by name to perform a specific task.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to invoke.",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to run the skill against.",
                        },
                        "input_data": {
                            "type": "object",
                            "description": "Input data for the skill.",
                        },
                    },
                    "required": ["skill_name", "task_id"],
                },
            },
            {
                "name": "call_mcp_tool",
                "description": "Call an external MCP tool (GitHub, Calendar, Slack, etc.).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "server_id": {"type": "string", "description": "MCP server ID."},
                        "tool_name": {
                            "type": "string",
                            "description": "Tool name on the MCP server.",
                        },
                        "arguments": {"type": "object", "description": "Tool arguments."},
                    },
                    "required": ["server_id", "tool_name"],
                },
            },
        ]

    async def _dispatch_tool(
        self, tool_name: str, tool_input: dict[str, Any], job: AgentJobEvent
    ) -> dict[str, Any]:
        """Dispatch a tool call and return the result dict."""
        if tool_name == "invoke_skill":
            return await self._tool_invoke_skill(tool_input, job)
        if tool_name == "call_mcp_tool":
            return await self._tool_call_mcp(tool_input)
        return {"error": f"Unknown tool: {tool_name}"}

    async def _tool_invoke_skill(self, args: dict[str, Any], job: AgentJobEvent) -> dict[str, Any]:
        """Invoke a skill via the dedicated sub-agent worker pool."""
        if self._worker_pool is None or self._skill_registry is None:
            return {
                "error": "Skill execution not available — worker pool or registry not configured."
            }

        skill_name = args.get("skill_name", "")
        task_id = args.get("task_id", job.task_id)
        input_data = args.get("input_data", {})

        try:
            skill_def = await self._skill_registry.get_skill_definition(skill_name)
        except Exception as exc:
            return {"error": f"Skill '{skill_name}' not found: {exc}"}

        from graphclaw.skills.models import SkillJob

        skill_job = SkillJob(
            skill_name=skill_name,
            task_id=task_id,
            session_id=job.session_id,
            input_data=input_data,
        )

        worker = self._worker_pool.get_idle_worker()
        if worker is None:
            return {"error": "No idle skill workers available. Try again shortly."}

        result = await worker.execute(skill_job, skill_def)
        return {
            "status": result.status.value,
            "output": result.output or "",
            "error": result.error,
            "tokens_used": result.tokens_used,
            "cost_usd": result.cost_usd,
        }

    async def _tool_call_mcp(self, args: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool via the MCP registry."""
        if self._mcp_registry is None:
            return {"error": "MCP registry not configured."}

        server_id = args.get("server_id", "")
        tool_name = args.get("tool_name", "")
        arguments = args.get("arguments", {})

        try:
            from graphclaw.mcp.client import MCPClient

            server_config = await self._mcp_registry.get(server_id)
            if server_config is None:
                return {"error": f"MCP server '{server_id}' not found."}

            start = time.monotonic()
            async with MCPClient(server_config) as client:
                result = await client.call_tool(tool_name, arguments)
            latency_ms = int((time.monotonic() - start) * 1000)

            return {"success": True, "content": result, "latency_ms": latency_ms}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, job: AgentJobEvent) -> None:
        """Emit heartbeat events at regular intervals while running."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            if self._state != RunnerState.RUNNING:
                break
            self._last_heartbeat = utcnow()
            await self._emit(AgentUpdateEventType.HEARTBEAT, job)
            self._audit_heartbeat(job)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit(
        self,
        event_type: AgentUpdateEventType,
        job: AgentJobEvent,
        message: str | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Publish an AgentUpdateEvent to the AGENT_UPDATES broker queue."""
        event = AgentUpdateEvent(
            event_type=event_type,
            agent_id=job.agent_id,
            task_id=job.task_id,
            session_id=job.session_id,
            parent_task_id=job.parent_task_id,
            batch_id=job.batch_id,
            message=message,
            status=status,
            duration_ms=duration_ms,
        )
        try:
            await self._broker.publish(AGENT_UPDATES, event.model_dump_json())
        except Exception as exc:
            logger.warning("SubAgentRunner: failed to publish event %s: %s", event_type, exc)

    # ------------------------------------------------------------------
    # Audit logging helpers
    # ------------------------------------------------------------------

    def _audit_started(self, job: AgentJobEvent) -> None:
        if self._logger:
            from graphclaw.infra.logger import AgentTaskStartedEvent

            self._logger.log(
                "INFO",
                "agent.task.started",
                job.session_id,
                **AgentTaskStartedEvent(
                    agent_id=job.agent_id,
                    task_id=job.task_id,
                    session_id=job.session_id,
                    parent_task_id=job.parent_task_id,
                    batch_id=job.batch_id,
                ).model_dump(),
            )

    def _audit_progress(self, job: AgentJobEvent, message: str, iteration: int) -> None:
        if self._logger:
            from graphclaw.infra.logger import AgentTaskProgressEvent

            self._logger.log(
                "INFO",
                "agent.task.progress",
                job.session_id,
                **AgentTaskProgressEvent(
                    agent_id=job.agent_id,
                    task_id=job.task_id,
                    session_id=job.session_id,
                    message=message,
                    iteration=iteration,
                ).model_dump(),
            )

    def _audit_completed(self, job: AgentJobEvent, status: str, duration_ms: int) -> None:
        if self._logger:
            from graphclaw.infra.logger import AgentTaskCompletedEvent

            self._logger.log(
                "INFO",
                "agent.task.completed",
                job.session_id,
                **AgentTaskCompletedEvent(
                    agent_id=job.agent_id,
                    task_id=job.task_id,
                    session_id=job.session_id,
                    status=status,
                    duration_ms=duration_ms,
                    parent_task_id=job.parent_task_id,
                    batch_id=job.batch_id,
                ).model_dump(),
            )

    def _audit_blocked(self, job: AgentJobEvent, reason: str) -> None:
        if self._logger:
            from graphclaw.infra.logger import AgentTaskBlockedEvent

            self._logger.log(
                "WARNING",
                "agent.task.blocked",
                job.session_id,
                **AgentTaskBlockedEvent(
                    agent_id=job.agent_id,
                    task_id=job.task_id,
                    session_id=job.session_id,
                    reason=reason,
                ).model_dump(),
            )

    def _audit_heartbeat(self, job: AgentJobEvent) -> None:
        if self._logger:
            from graphclaw.infra.logger import AgentHeartbeatEvent

            self._logger.log(
                "DEBUG",
                "agent.heartbeat",
                job.session_id,
                **AgentHeartbeatEvent(
                    agent_id=job.agent_id,
                    task_id=job.task_id,
                    session_id=job.session_id,
                ).model_dump(),
            )
