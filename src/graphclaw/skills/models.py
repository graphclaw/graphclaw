"""graphclaw.skills.models — Skill domain models for the GraphClaw skill runtime.

Description
-----------
Defines all Pydantic v2 models and enumerations used by the skill execution
subsystem: lifecycle states, skill definitions parsed from SKILL.md files,
job requests submitted to the worker pool, execution results, per-worker
status snapshots, and heartbeat configuration.

Design Patterns
---------------
- Value Objects: ``SkillDefinition``, ``SkillJob``, ``SkillResult``, and
  ``WorkerStatus`` are immutable-by-convention data containers with no
  behaviour.  Callers construct new instances rather than mutating existing
  ones.
- Enums: ``SkillStatus`` and ``ThreadState`` use ``str`` as the mixin type
  so they serialise transparently with Pydantic and JSON.

Public API
----------
- SkillStatus: Lifecycle states for a skill execution (IDLE, RUNNING, …).
- ThreadState: Lifecycle states for a worker thread (SPAWNING, RUNNING, …).
- SkillDefinition: Complete representation of a parsed SKILL.md file.
- SkillJob: A single job submitted to the worker pool.
- SkillResult: The outcome of a completed skill execution.
- WorkerStatus: A point-in-time status snapshot for a single SkillWorker.
- HeartbeatConfig: Tuning parameters for the HeartbeatMonitor.

Dependencies
------------
- pydantic: BaseModel, field defaults.
- datetime: datetime type used in timestamps.
- enum: Enum base class.

Notes
-----
``SkillJob`` uses ``priority: int = 0`` where higher values indicate higher
importance.  The WorkerPool stores jobs in a ``PriorityQueue`` as
``(-priority, job)`` so higher-priority jobs are dequeued first.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SkillStatus(str, Enum):
    """Lifecycle state of a skill execution."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class ThreadState(str, Enum):
    """Lifecycle state of a worker thread."""

    SPAWNING = "SPAWNING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


# ---------------------------------------------------------------------------
# Skill definition (parsed from SKILL.md)
# ---------------------------------------------------------------------------


class SkillDefinition(BaseModel):
    """Parsed SKILL.md file representation.

    A ``SkillDefinition`` captures all configuration declared in the YAML
    frontmatter of a SKILL.md file plus the markdown body, which is used as
    the system prompt when the skill is invoked.

    Attributes
    ----------
    name:
        Unique skill identifier, taken from the ``name`` frontmatter key.
    description:
        Short human-readable description of what the skill does.
    version:
        Semantic version string (defaults to ``"1.0.0"``).
    model:
        LiteLLM-compatible model string (defaults to ``"claude-sonnet-4-20250514"``).
    max_tokens:
        Maximum tokens in the LLM completion (defaults to 4096).
    temperature:
        Sampling temperature for the LLM (defaults to 0.0 — deterministic).
    system_prompt:
        The markdown body of SKILL.md, used verbatim as the LLM system prompt.
    tools:
        Tool names this skill is allowed to invoke.
    tags:
        Arbitrary classification tags for the skill.
    timeout_seconds:
        Hard execution timeout in seconds (defaults to 300).
    """

    name: str
    description: str = ""
    version: str = "1.0.0"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0
    system_prompt: str = ""
    tools: list[str] = []
    tags: list[str] = []
    timeout_seconds: int = 300


# ---------------------------------------------------------------------------
# Skill job
# ---------------------------------------------------------------------------


class SkillJob(BaseModel):
    """A job submitted to the skill worker pool.

    Attributes
    ----------
    job_id:
        Unique identifier for this job instance.
    skill_name:
        Name of the ``SkillDefinition`` to execute.
    task_id:
        GraphClaw task ID this job is associated with.
    session_id:
        Distributed tracing session identifier.
    input_data:
        Arbitrary key-value data passed as the user message to the LLM.
    priority:
        Dispatch priority; higher values are processed first.
    created_at:
        UTC timestamp when the job was created.
    timeout_seconds:
        Per-job timeout override (defaults to 300).
    """

    job_id: str
    skill_name: str
    task_id: str
    session_id: str
    input_data: dict = {}
    priority: int = 0
    created_at: datetime
    timeout_seconds: int = 300


# ---------------------------------------------------------------------------
# Skill result
# ---------------------------------------------------------------------------


class SkillResult(BaseModel):
    """Result from a completed skill execution.

    Attributes
    ----------
    job_id:
        Identifier of the originating ``SkillJob``.
    skill_name:
        Name of the skill that was executed.
    task_id:
        GraphClaw task ID the job was associated with.
    session_id:
        Distributed tracing session identifier.
    status:
        Terminal ``SkillStatus`` value for this execution.
    output:
        Raw text content returned by the LLM (empty on failure).
    error:
        Error description if the job did not complete successfully.
    started_at:
        UTC timestamp when execution started.
    completed_at:
        UTC timestamp when execution ended (success or failure).
    tokens_used:
        Total tokens consumed (prompt + completion).
    cost_usd:
        Estimated USD cost of the LLM call (0.0 if unavailable).
    """

    job_id: str
    skill_name: str
    task_id: str
    session_id: str
    status: SkillStatus
    output: str = ""
    error: str | None = None
    started_at: datetime
    completed_at: datetime
    tokens_used: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Worker status
# ---------------------------------------------------------------------------


class WorkerStatus(BaseModel):
    """Status snapshot for a single SkillWorker.

    Attributes
    ----------
    worker_id:
        Unique identifier for the worker thread.
    state:
        Current ``ThreadState`` of the worker.
    current_job_id:
        ID of the job being processed, or ``None`` if idle.
    last_heartbeat:
        UTC timestamp of the most recent heartbeat tick.
    jobs_completed:
        Total number of jobs successfully completed by this worker.
    jobs_failed:
        Total number of jobs that failed or timed out on this worker.
    """

    worker_id: str
    state: ThreadState
    current_job_id: str | None = None
    last_heartbeat: datetime | None = None
    jobs_completed: int = 0
    jobs_failed: int = 0


# ---------------------------------------------------------------------------
# Heartbeat configuration
# ---------------------------------------------------------------------------


class HeartbeatConfig(BaseModel):
    """Configuration for the HeartbeatMonitor.

    Attributes
    ----------
    interval_seconds:
        How often the monitor checks worker heartbeats (default 300 s = 5 min).
    timeout_seconds:
        How long a worker can remain in RUNNING state without a heartbeat
        update before being considered timed out (default 900 s = 15 min).
    max_respawn_attempts:
        Maximum number of respawn attempts before the worker is marked as
        permanently failed (default 3).
    """

    interval_seconds: float = 300.0
    timeout_seconds: float = 900.0
    max_respawn_attempts: int = 3
