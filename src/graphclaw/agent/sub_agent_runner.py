# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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

from graphclaw.agent.prompt_budget import dumps_tool_result, truncate_tool_result
from graphclaw.config import config
from graphclaw.infra.broker import AGENT_UPDATES, MessageBroker
from graphclaw.infra.logging.events import AgentToolCallEvent
from graphclaw.models.base import utcnow

if TYPE_CHECKING:
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
    user_id: str = ""  # Explicit user ID — do NOT derive from session_id
    agent_source: str = "user"  # "system" | "user" — determines profile resolution path
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
    # Authoritative user_id, carried straight from AgentJobEvent — consumers
    # (e.g. ResultCollector) must use this, not re-derive it by splitting
    # session_id, which assumes a "ses-{user_id}-{timestamp}" shape that
    # breaks for any user_id containing a hyphen.
    user_id: str = ""
    parent_task_id: str | None = None
    batch_id: str = ""
    message: str | None = None
    status: str | None = None  # COMPLETED | FAILED | TIMED_OUT | CANCELLED
    duration_ms: int | None = None  # completed events only
    emitted_at: datetime = Field(default_factory=utcnow)


class RunnerState(str, Enum):
    """Lifecycle states for a SubAgentRunner."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


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
    execution_timeout_seconds:
        Hard timeout for the full delegated run. When exceeded, the runner
        transitions to TIMED_OUT and emits a BLOCKED update.
    tool_timeout_seconds:
        Per-tool-call timeout applied to ``invoke_skill`` and ``call_mcp_tool``.
    tool_max_retries:
        Maximum retries for retry-eligible tool calls.
    retry_backoff_base_ms:
        Base backoff in milliseconds between retry attempts.
    retry_backoff_max_ms:
        Maximum backoff in milliseconds between retry attempts.
    retryable_skills:
        Skill-name allowlist for retry-safe ``invoke_skill`` calls.
    retryable_mcp_tools:
        MCP tool allowlist for retry-safe ``call_mcp_tool`` calls.
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
        heartbeat_interval: int = 60,
        execution_timeout_seconds: int = 600,
        tool_timeout_seconds: int = 120,
        tool_max_retries: int = 0,
        retry_backoff_base_ms: int = 200,
        retry_backoff_max_ms: int = 1000,
        retryable_skills: set[str] | None = None,
        retryable_mcp_tools: set[str] | None = None,
    ) -> None:
        self._runner_id = runner_id
        self._broker = broker
        self._llm = llm_client
        self._storage = storage
        self._worker_pool = worker_pool
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._heartbeat_interval = heartbeat_interval
        self._execution_timeout_seconds = max(1, execution_timeout_seconds)
        self._tool_timeout_seconds = max(1, tool_timeout_seconds)
        self._tool_max_retries = max(0, tool_max_retries)
        self._retry_backoff_base_ms = max(0, retry_backoff_base_ms)
        self._retry_backoff_max_ms = max(self._retry_backoff_base_ms, retry_backoff_max_ms)
        self._retryable_skills = retryable_skills or set()
        self._retryable_mcp_tools = retryable_mcp_tools or set()

        self._state: RunnerState = RunnerState.IDLE
        self._current_job: AgentJobEvent | None = None
        self._started_at: datetime | None = None
        self._last_heartbeat: datetime | None = None
        # Resolved once per job in _run_llm_loop; None -> LLMRole.SUBAGENT
        # routing default on self._llm applies.
        self._agent_model: str | None = None

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
            RunnerState.CANCELLED,
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
            Final status string: ``"COMPLETED"``, ``"FAILED"``, ``"TIMED_OUT"``,
            or ``"CANCELLED"``.
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
        blocked_reason: str | None = None
        cancellation_exc: asyncio.CancelledError | None = None
        try:
            await asyncio.wait_for(
                self._run_llm_loop(job),
                timeout=self._execution_timeout_seconds,
            )
            self._state = RunnerState.COMPLETED
        except asyncio.TimeoutError:
            final_status = "TIMED_OUT"
            self._state = RunnerState.TIMED_OUT
            blocked_reason = (
                f"Runner exceeded timeout of {self._execution_timeout_seconds}s "
                f"for task {job.task_id}"
            )
            logger.warning("SubAgentRunner %s timed out: %s", self._runner_id, blocked_reason)
        except asyncio.CancelledError as exc:
            final_status = "CANCELLED"
            self._state = RunnerState.CANCELLED
            blocked_reason = f"Runner cancelled while executing task {job.task_id}"
            cancellation_exc = exc
        except Exception as exc:
            final_status = "FAILED"
            self._state = RunnerState.FAILED
            logger.exception(
                "SubAgentRunner %s failed on task %s: %s", self._runner_id, job.task_id, exc
            )
            blocked_reason = f"Runner failed: {exc!s}"
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            if blocked_reason:
                await self._emit(
                    AgentUpdateEventType.BLOCKED,
                    job,
                    message=blocked_reason,
                )
                self._audit_blocked(job, reason=blocked_reason)

            duration_ms = int((time.monotonic() - start_ts) * 1000)
            await self._emit(
                AgentUpdateEventType.COMPLETED,
                job,
                message=f"Task {job.task_id} finished with status {final_status}",
                status=final_status,
                duration_ms=duration_ms,
            )
            self._audit_completed(job, status=final_status, duration_ms=duration_ms)
            # Append execution summary to agent's working memory so the
            # Intelligence Hub displays a timeline of what each agent did.
            await self._write_context_note(job, final_status, duration_ms)
            self._current_job = None

        if cancellation_exc is not None:
            raise cancellation_exc

        return final_status

    # ------------------------------------------------------------------
    # LLM tool-use loop
    # ------------------------------------------------------------------

    # Legacy literal that main_orchestrator._tool_create_agent used to stamp
    # into every new agent's config.json before role-based routing existed.
    # Agents created before this change carry it on disk; treat it the same
    # as "unset" rather than requiring a data migration.
    _LEGACY_HARDCODED_MODEL = "claude-sonnet-4-20250514"

    async def _resolve_model(self, job: AgentJobEvent) -> str | None:
        """Resolve this agent's model override from config.json.

        config.json's ``llm_model`` field wins over the ``LLMRole.SUBAGENT``
        routing default. Returns ``None`` when unset, blank, missing, or the
        pre-routing legacy literal — in every case ``self._llm`` (a
        role-bound client) applies its own default.
        """
        if self._storage is None or not job.user_id:
            return None
        from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

        try:
            config_path = StoragePaths.agent_config(job.user_id, job.agent_id)
        except ValueError:
            return None
        try:
            raw = await self._storage.read(config_path)
        except FileNotFoundError:
            return None
        try:
            cfg = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning(
                "SubAgentRunner: malformed config.json for agent '%s' (user=%s): %s",
                job.agent_id,
                job.user_id,
                exc,
            )
            return None

        value = cfg.get("llm_model") if isinstance(cfg, dict) else None
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or value == self._LEGACY_HARDCODED_MODEL:
            return None
        return value

    async def _run_llm_loop(self, job: AgentJobEvent) -> None:
        """Run a multi-turn LLM tool-use loop for the delegated task."""
        from graphclaw.llm.base import LLMMessage  # local import

        self._agent_model = await self._resolve_model(job)
        system_prompt = await self._build_system_prompt(job)
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(
                role="user",
                content=(
                    f"You have been delegated the following task:\n\n"
                    f"Task ID: {job.task_id}\n"
                    f"Instructions: {job.instructions}\n\n"
                    f"Use the available tools to complete this task. "
                    f"Report your findings and actions clearly."
                ),
            ),
        ]
        tools = self._build_tools()

        for iteration in range(_MAX_ITERATIONS):
            response = await self._llm.complete(
                messages=messages,
                tools=tools,
                max_tokens=4096,
                model=self._agent_model,
            )

            # Emit progress on each iteration
            content_text = response.content
            if content_text:
                await self._emit(
                    AgentUpdateEventType.PROGRESS,
                    job,
                    message=content_text[:200],
                )
                self._audit_progress(job, message=content_text[:200], iteration=iteration)

            tool_calls = response.tool_calls
            if not tool_calls:
                # No more tool calls — done
                break

            # Execute each tool call and append results — truncated the same
            # way as the orchestrator's agentic loop (see
            # MainOrchestrator._tool_result_message), so one large tool
            # result cannot alone blow the sub-agent's prompt budget either.
            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_input = tool_call.arguments
                tool_id = tool_call.id
                result = await self._dispatch_tool(tool_name, tool_input, job)
                content = truncate_tool_result(
                    dumps_tool_result(result), config.context.tool_result_max_chars
                )
                tool_results.append(
                    LLMMessage(
                        role="tool",
                        content=content,
                        tool_call_id=tool_id,
                    )
                )

            # Append assistant turn + tool results to conversation
            messages.append(
                LLMMessage(role="assistant", content=response.content, tool_calls=tool_calls)
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
            f"You cannot delegate further — complete the task directly using available tools.\n\n"
            f"## Working Memory\n"
            f"Use the `update_working_memory` tool to record noteworthy observations as you work.\n"
            f"Call it after each significant finding, decision, or action — for example:\n"
            f"  - After reading a batch of messages: record what you found and any patterns.\n"
            f"  - When you match a message to a task: record the match and required action.\n"
            f"  - When you complete an action: record what was done and the outcome.\n"
            f"Notes must be concise (one sentence), factual, and free of PII.\n\n"
            f"## Deliverables\n"
            f"When you complete your task and produce a final deliverable (email draft, report, "
            f"summary, etc.), use the `save_output` tool to save it. This ensures the orchestrator "
            f"can retrieve and use your work. For example:\n"
            f"  - Email drafts: save_output(filename='email-draft.md', content='...')\n"
            f"  - Analysis reports: save_output(filename='report.md', content='...')\n"
            f"  - Summaries: save_output(filename='summary.txt', content='...')\n"
        )
        if self._storage:
            try:
                from graphclaw.infra.storage import StoragePaths

                # All agents have user-scoped working memory so the Intelligence
                # Hub can display a per-user context timeline for every agent.
                # System agents use a shared profile.md but their working memory
                # (context.md) is always stored under {user_id}/agents/{agent_id}/.
                user_id = job.user_id
                if job.agent_source == "system":
                    # System agents: shared profile, user-scoped working memory
                    profile_path = StoragePaths.system_agent_profile(job.agent_id)
                    context_path = StoragePaths.agent_memory_working(user_id, job.agent_id)
                else:
                    # User agents: profile from {user_id}/agents/{agent_id}/profile.md
                    profile_path = StoragePaths.agent_profile(user_id, job.agent_id)
                    context_path = StoragePaths.agent_memory_working(user_id, job.agent_id)

                try:
                    profile_bytes = await self._storage.read(profile_path)
                    if profile_bytes:
                        base += f"\n## Agent Profile\n{profile_bytes.decode(errors='replace')}\n"
                except FileNotFoundError:
                    logger.debug(
                        "SubAgentRunner: no profile found for agent '%s' (source=%s)",
                        job.agent_id,
                        job.agent_source,
                    )

                if context_path:
                    try:
                        ctx_bytes = await self._storage.read(context_path)
                        if ctx_bytes:
                            base += f"\n## Working Context\n{ctx_bytes.decode(errors='replace')}\n"
                    except FileNotFoundError:
                        pass

                # Load episodic memory — scoped to this delegated task via
                # keyword+recency relevance, not a full unscoped scan. The
                # delegation instructions ARE the relevance query, so this
                # costs nothing extra beyond what the runner already knows.
                # Previously this loaded ALL active entries against a
                # hardcoded token_budget = 80_000, unscoped to the task —
                # able on its own to dwarf every other section of this
                # otherwise well-isolated 2-message sub-agent prompt.
                if user_id:
                    from graphclaw.agent.episodic_recall import recall_episodic  # noqa: PLC0415

                    cfg = config.context
                    query = f"{job.instructions} {job.task_id}".strip()
                    matches = await recall_episodic(
                        self._storage,
                        user_id=user_id,
                        agent_id=job.agent_id,
                        query=query,
                        limit=cfg.subagent_episodic_max_entries,
                        max_chars=cfg.subagent_episodic_max_chars,
                    )
                    if matches:
                        episodic_sections = [f"\n### {m.name}\n{m.content}\n" for m in matches]
                        base += "\n## Episodic Memory\n" + "".join(episodic_sections)

                    # Load semantic memory index (topics loaded on demand via read_memory tool)
                    try:
                        from graphclaw.api.intelligence import SemanticMemoryIndex

                        index_path = StoragePaths.agent_memory_semantic_index(user_id, job.agent_id)
                        index_bytes = await self._storage.read(index_path)
                        index = SemanticMemoryIndex.model_validate_json(index_bytes)
                        if index.topics:
                            topic_names = [t.name for t in index.topics]
                            lines = [f"- **{t.name}**: {t.description}" for t in index.topics]
                            base += (
                                "\n## Semantic Memory\n"
                                "The following knowledge topics are available. "
                                "Use the `read_memory` tool to load any topic before using it.\n\n"
                                + "\n".join(lines)
                                + "\n"
                            )
                            logger.info(
                                "agent.semantic_memory_index_loaded",
                                extra={
                                    "event_type": "agent.semantic_memory_index_loaded",
                                    "user_id": user_id,
                                    "agent_id": job.agent_id,
                                    "task_id": job.task_id,
                                    "session_id": job.session_id,
                                    "topic_names": topic_names,
                                    "section": "Semantic Memory",
                                },
                            )
                    except FileNotFoundError:
                        pass  # No index yet — no semantic memory section
                    except Exception as exc:
                        logger.debug(
                            "SubAgentRunner: could not load semantic memory index: %s", exc
                        )

            except Exception as exc:
                logger.debug("SubAgentRunner: could not load profile/context: %s", exc)

        # Overall cap: sub-agents run on the same local model as the
        # orchestrator and need the same discipline. Each section above is
        # already individually scoped (episodic via recall_episodic,
        # semantic via index-only), but nothing previously bounded the
        # assembled total — a large profile.md or working context could
        # still push the whole prompt arbitrarily high.
        cap = config.context.subagent_prompt_max_chars
        if cap > 0 and len(base) > cap:
            base = base[:cap].rstrip() + f"\n…(system prompt truncated, {len(base)} chars total)"
        return base

    async def _write_context_note(
        self, job: AgentJobEvent, final_status: str, duration_ms: int
    ) -> None:
        """Append a timestamped execution summary to the agent's working context.md.

        Keeps a rolling timeline in ``{user_id}/agents/{agent_id}/memory/working/context.md``
        so the Intelligence Hub can show what each agent has done over time.
        Uses the same JSON-line format as ``InboundIntelligenceAgent`` for consistency.
        """
        if self._storage is None:
            return
        try:
            from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

            context_path = StoragePaths.agent_memory_working(job.user_id, job.agent_id)
            ts_iso = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            entry = json.dumps(
                {
                    "timestamp": ts_iso,
                    "source": "sub_agent_runner",
                    "agent_id": job.agent_id,
                    "task_id": job.task_id,
                    "status": final_status,
                    "duration_ms": duration_ms,
                    "note": (job.instructions or "")[:200],
                },
                ensure_ascii=True,
            )
            try:
                raw = await self._storage.read(context_path)
                ctx = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                ctx = ""
            if ctx and not ctx.endswith("\n"):
                ctx += "\n"
            ctx += entry + "\n"
            await self._storage.write(context_path, ctx.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("SubAgentRunner: could not write context note: %s", exc)

    def _build_tools(self) -> list[Any]:
        """Return the tool definitions available to sub-agents.

        Sub-agents can invoke skills, MCP tools, and read semantic memory.
        Delegation is excluded (flat delegation only).
        """
        from graphclaw.llm.base import ToolDefinition as _TD  # local import

        return [
            _TD(
                name="invoke_skill",
                description="Invoke a skill by name to perform a specific task.",
                parameters={
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
            ),
            _TD(
                name="call_mcp_tool",
                description="Call an external MCP tool (GitHub, Calendar, Slack, etc.).",
                parameters={
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
            ),
            _TD(
                name="update_working_memory",
                description=(
                    "Append a concise, factual note to your working memory context.md. "
                    "Call this after each significant finding, decision, or completed action "
                    "so your activity is visible in the Intelligence Hub timeline. "
                    "Notes must be one sentence, factual, and free of PII."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "description": "One-sentence factual observation to record.",
                        },
                    },
                    "required": ["note"],
                },
            ),
            _TD(
                name="read_memory",
                description=(
                    "Load a specific semantic memory topic into context. "
                    "Call this when you need knowledge from a topic listed in the Semantic Memory index."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "The topic name exactly as shown in the Semantic Memory index.",
                        },
                    },
                    "required": ["topic"],
                },
            ),
            _TD(
                name="save_output",
                description=(
                    "Save your final work product or deliverable to an output file. "
                    "Use this when you've completed your task and need to return results "
                    "to the orchestrator (e.g., drafted email, analysis report, summary). "
                    "The output will be automatically included in the task intelligence."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Output filename (e.g., 'email-draft.md', 'report.md', 'summary.txt').",
                        },
                        "content": {
                            "type": "string",
                            "description": "The complete output content to save.",
                        },
                    },
                    "required": ["filename", "content"],
                },
            ),
        ]

    async def _dispatch_tool(
        self, tool_name: str, tool_input: dict[str, Any], job: AgentJobEvent
    ) -> dict[str, Any]:
        """Dispatch a tool call and return the result dict."""
        if tool_name == "invoke_skill":
            skill_name = str(tool_input.get("skill_name", "")).strip()
            retry_allowed = skill_name in self._retryable_skills
            task_id = str(tool_input.get("task_id", job.task_id)).strip() or job.task_id
            return await self._execute_tool_with_retries(
                tool_name=tool_name,
                op=lambda: self._tool_invoke_skill(tool_input, job),
                retry_allowed=retry_allowed,
                is_retryable_result=self._is_retryable_skill_result,
                job=job,
                task_id=task_id,
            )

        if tool_name == "call_mcp_tool":
            server_id = str(tool_input.get("server_id", "")).strip()
            mcp_tool = str(tool_input.get("tool_name", "")).strip()
            retry_key = f"{server_id}:{mcp_tool}" if server_id and mcp_tool else ""
            retry_allowed = (
                mcp_tool in self._retryable_mcp_tools or retry_key in self._retryable_mcp_tools
            )
            return await self._execute_tool_with_retries(
                tool_name=tool_name,
                op=lambda: self._tool_call_mcp(tool_input),
                retry_allowed=retry_allowed,
                is_retryable_result=self._is_retryable_mcp_result,
                job=job,
                task_id=job.task_id,
            )

        if tool_name == "update_working_memory":
            return await self._execute_tool_with_retries(
                tool_name=tool_name,
                op=lambda: self._tool_update_working_memory(tool_input, job),
                retry_allowed=False,
                is_retryable_result=lambda _result: False,
                job=job,
                task_id=job.task_id,
            )

        if tool_name == "read_memory":
            return await self._execute_tool_with_retries(
                tool_name=tool_name,
                op=lambda: self._tool_read_memory(tool_input, job),
                retry_allowed=False,
                is_retryable_result=lambda _result: False,
                job=job,
                task_id=job.task_id,
            )

        if tool_name == "save_output":
            return await self._execute_tool_with_retries(
                tool_name=tool_name,
                op=lambda: self._tool_save_output(tool_input, job),
                retry_allowed=False,
                is_retryable_result=lambda _result: False,
                job=job,
                task_id=job.task_id,
            )

        result = {"error": f"Unknown tool: {tool_name}"}
        self._log_tool_call_event(
            tool_name=tool_name,
            job=job,
            result=result,
            latency_ms=0,
            attempt=1,
            task_id=job.task_id,
        )
        return result

    async def _execute_tool_with_retries(
        self,
        tool_name: str,
        op: Any,
        retry_allowed: bool,
        is_retryable_result: Any,
        job: AgentJobEvent,
        task_id: str,
    ) -> dict[str, Any]:
        """Execute a tool op with bounded retries for transient failures."""
        max_attempts = 1 + (self._tool_max_retries if retry_allowed else 0)

        for attempt in range(1, max_attempts + 1):
            attempt_t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(op(), timeout=self._tool_timeout_seconds)
            except asyncio.TimeoutError:
                result = {
                    "error": f"Tool '{tool_name}' exceeded timeout of {self._tool_timeout_seconds}s"
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "SubAgentRunner %s tool '%s' failed: %s",
                    self._runner_id,
                    tool_name,
                    exc,
                )
                result = {"error": f"Tool '{tool_name}' failed: {exc!s}"}

            self._log_tool_call_event(
                tool_name=tool_name,
                job=job,
                result=result,
                latency_ms=int((time.monotonic() - attempt_t0) * 1000),
                attempt=attempt,
                task_id=task_id,
            )

            should_retry = attempt < max_attempts and is_retryable_result(result)
            if not should_retry:
                return result

            backoff_ms = min(
                self._retry_backoff_max_ms,
                self._retry_backoff_base_ms * (2 ** (attempt - 1)),
            )
            await asyncio.sleep(backoff_ms / 1000)

        return result

    def _is_retryable_skill_result(self, result: dict[str, Any]) -> bool:
        """Return True when skill result appears to be transient."""
        error = str(result.get("error", "")).lower()
        if not error:
            return False
        return "no idle skill workers" in error or "timeout" in error

    def _is_retryable_mcp_result(self, result: dict[str, Any]) -> bool:
        """Return True when MCP result appears to be transient."""
        if result.get("success") is True:
            return False
        error = str(result.get("error", "")).lower()
        if not error:
            return False
        transient_markers = (
            "timeout",
            "temporarily unavailable",
            "connection",
            "reset",
            "refused",
            "unreachable",
        )
        return any(marker in error for marker in transient_markers)

    def _log_tool_call_event(
        self,
        tool_name: str,
        job: AgentJobEvent,
        result: dict[str, Any],
        latency_ms: int,
        attempt: int,
        task_id: str | None,
    ) -> None:
        """Emit a normalized ``agent.tool_call`` event for each tool attempt."""
        success = not bool(result.get("error")) and result.get("success") is not False
        event = AgentToolCallEvent(
            tool_name=tool_name,
            user_id=job.user_id,
            latency_ms=latency_ms,
            session_id=job.session_id,
            task_id=task_id,
            success=success,
            attempt=attempt,
        )
        logger.info(
            "agent.tool_call",
            extra={"event_type": "agent.tool_call", **event.model_dump()},
        )

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
            # get_skill_definition is (user_id, skill_id) — the previous
            # single-arg call always raised TypeError, so every sub-agent
            # skill invocation failed and surfaced as "not found" to the LLM.
            skill_def = await self._skill_registry.get_skill_definition(job.user_id, skill_name)
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

    async def _tool_update_working_memory(
        self, args: dict[str, Any], job: AgentJobEvent
    ) -> dict[str, Any]:
        """Append one agent-authored note to the agent's working context.md.

        Called when the LLM invokes the ``update_working_memory`` tool mid-execution.
        Uses the same JSON-line format as ``InboundIntelligenceAgent`` so the
        Intelligence Hub displays all sources in a unified timeline.
        """
        note = str(args.get("note", "")).strip()
        if not note:
            return {"error": "note must be a non-empty string"}
        # Cap length to prevent prompt-injection abuse via oversized notes.
        note = note[:500]

        if self._storage is None:
            return {"error": "Storage not available."}

        try:
            from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

            context_path = StoragePaths.agent_memory_working(job.user_id, job.agent_id)
            ts_iso = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            entry = json.dumps(
                {
                    "timestamp": ts_iso,
                    "source": "agent_tool",
                    "agent_id": job.agent_id,
                    "task_id": job.task_id,
                    "note": note,
                },
                ensure_ascii=True,
            )
            try:
                raw = await self._storage.read(context_path)
                ctx = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                ctx = ""
            if ctx and not ctx.endswith("\n"):
                ctx += "\n"
            ctx += entry + "\n"
            await self._storage.write(context_path, ctx.encode("utf-8"))
            return {"ok": True, "recorded": note}
        except Exception as exc:  # noqa: BLE001
            logger.warning("SubAgentRunner: update_working_memory failed: %s", exc)
            return {"error": str(exc)}

    async def _tool_read_memory(self, args: dict[str, Any], job: AgentJobEvent) -> dict[str, Any]:
        """Load a semantic memory topic file into context."""
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return {"error": "topic must be a non-empty string"}
        if self._storage is None:
            return {"error": "Storage not available."}
        try:
            from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

            path = StoragePaths.agent_memory_semantic_topic(job.user_id, job.agent_id, topic)
            content = (await self._storage.read(path)).decode(errors="replace")
            logger.info(
                "agent.read_memory",
                extra={
                    "event_type": "agent.read_memory",
                    "user_id": job.user_id,
                    "agent_id": job.agent_id,
                    "task_id": job.task_id,
                    "session_id": job.session_id,
                    "topic": topic,
                },
            )
            return {"topic": topic, "content": content}
        except FileNotFoundError:
            return {"error": f"Semantic memory topic '{topic}' not found."}
        except Exception as exc:  # noqa: BLE001
            logger.warning("SubAgentRunner: read_memory failed topic=%s: %s", topic, exc)
            return {"error": str(exc)}

    async def _tool_save_output(self, args: dict[str, Any], job: AgentJobEvent) -> dict[str, Any]:
        """Save agent output to the output/ directory for orchestrator retrieval.

        Writes to {user_id}/agents/{agent_id}/output/{filename} so ResultCollector
        can read it and include it in the task intelligence field.
        """
        filename = str(args.get("filename", "")).strip()
        content = str(args.get("content", "")).strip()

        if not filename:
            return {"error": "filename must be a non-empty string"}
        if not content:
            return {"error": "content must be a non-empty string"}

        # Sanitize filename to prevent path traversal
        safe_filename = filename.replace("..", "").replace("/", "").replace("\\", "")
        if not safe_filename:
            return {"error": "Invalid filename"}

        if self._storage is None:
            return {"error": "Storage not available."}

        try:
            from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

            output_path = (
                f"{StoragePaths.agent_root(job.user_id, job.agent_id)}output/{safe_filename}"
            )
            await self._storage.write(output_path, content.encode("utf-8"))

            logger.info(
                "agent.save_output",
                extra={
                    "event_type": "agent.save_output",
                    "user_id": job.user_id,
                    "agent_id": job.agent_id,
                    "task_id": job.task_id,
                    "session_id": job.session_id,
                    "filename": safe_filename,
                    "size_bytes": len(content.encode("utf-8")),
                },
            )
            return {
                "ok": True,
                "filename": safe_filename,
                "path": output_path,
                "size_bytes": len(content.encode("utf-8")),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("SubAgentRunner: save_output failed filename=%s: %s", safe_filename, exc)
            return {"error": str(exc)}

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
            user_id=job.user_id,
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
        logger.info(
            "agent.task.started",
            extra={
                "event_type": "agent.task.started",
                "agent_id": job.agent_id,
                "task_id": job.task_id,
                "session_id": job.session_id,
                "parent_task_id": job.parent_task_id,
                "batch_id": job.batch_id,
            },
        )

    def _audit_progress(self, job: AgentJobEvent, message: str, iteration: int) -> None:
        logger.info(
            "agent.task.progress",
            extra={
                "event_type": "agent.task.progress",
                "agent_id": job.agent_id,
                "task_id": job.task_id,
                "session_id": job.session_id,
                "progress_message": message,
                "iteration": iteration,
            },
        )

    def _audit_completed(self, job: AgentJobEvent, status: str, duration_ms: int) -> None:
        logger.info(
            "agent.task.completed",
            extra={
                "event_type": "agent.task.completed",
                "agent_id": job.agent_id,
                "task_id": job.task_id,
                "session_id": job.session_id,
                "status": status,
                "duration_ms": duration_ms,
                "parent_task_id": job.parent_task_id,
                "batch_id": job.batch_id,
            },
        )

    def _audit_blocked(self, job: AgentJobEvent, reason: str) -> None:
        logger.warning(
            "agent.task.blocked",
            extra={
                "event_type": "agent.task.blocked",
                "agent_id": job.agent_id,
                "task_id": job.task_id,
                "session_id": job.session_id,
                "reason": reason,
            },
        )

    def _audit_heartbeat(self, job: AgentJobEvent) -> None:
        logger.debug(
            "agent.heartbeat",
            extra={
                "event_type": "agent.heartbeat",
                "agent_id": job.agent_id,
                "task_id": job.task_id,
                "session_id": job.session_id,
            },
        )
