# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.run_events — Pydantic models for agent run-trace events.

Description
-----------
Defines the contract for structured events emitted during a single agent chat
run.  Every event carries a monotonic ``event_seq`` so consumers can detect
gaps and reconstruct the exact ordering.

Each run begins with ``run.started`` and must terminate with exactly one of
``run.completed`` or ``run.failed`` regardless of outcome.

Design Patterns
---------------
- Sealed discriminated union: ``AgentRunEvent.payload`` is typed using a
  ``Union`` of payload models; each payload carries a ``schema_version``
  field so consumers can handle forward-compatible changes.
- Allowlist sanitization: Helper ``sanitize_text`` strips keys matching a
  deny-list pattern and caps text length to prevent accidental secret leakage.

Public API
----------
- AgentRunEvent: Main event envelope.
- RunEventType: String enum of all event type identifiers.
- make_event: Factory for building an ``AgentRunEvent`` with auto-generated
  timestamp and configurable payload.

All payload model classes are exported for type narrowing by consumers.

Dependencies
------------
- pydantic: BaseModel, Field (third-party).
- datetime: for UTC timestamps (stdlib).
- re: for sanitization (stdlib).
- typing: Literal, Union (stdlib).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"

# Keys whose values are stripped from summaries to prevent secret leakage
_SECRET_KEY_RE = re.compile(r"secret|token|password|key|credential|auth|bearer|api_key", re.I)

_MAX_ARGS_LEN = 200
_MAX_RESULT_LEN = 300
_MAX_ERROR_LEN = 200


# ---------------------------------------------------------------------------
# Sanitization helper
# ---------------------------------------------------------------------------


def sanitize_text(text: str, max_len: int = _MAX_RESULT_LEN) -> str:
    """Truncate text to *max_len* characters.

    Does not parse or strip JSON structures — callers should pre-sanitize
    dicts via ``sanitize_args`` before converting to a string summary.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def sanitize_args(args: dict[str, Any], max_len: int = _MAX_ARGS_LEN) -> str:
    """Build a safe summary string from a tool argument dict.

    Keys matching the secret deny-list are replaced with ``"[redacted]"``.
    The result is truncated to ``max_len`` characters.
    """
    safe: dict[str, Any] = {}
    for k, v in args.items():
        if _SECRET_KEY_RE.search(k):
            safe[k] = "[redacted]"
        else:
            safe[k] = v
    raw = str(safe)
    return sanitize_text(raw, max_len)


# ---------------------------------------------------------------------------
# Event type identifiers
# ---------------------------------------------------------------------------


class RunEventType:
    """String constants for all event types.

    Using a class with class attributes rather than an Enum so that
    downstream consumers can do simple string comparisons without importing
    the enum class.
    """

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"

    PLAN_PROPOSED = "plan.proposed"

    ASSISTANT_DELTA = "assistant.delta"
    ASSISTANT_FINAL = "assistant.final"

    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    SKILL_STARTED = "skill.started"
    SKILL_PROGRESS = "skill.progress"
    SKILL_COMPLETED = "skill.completed"
    SKILL_FAILED = "skill.failed"

    DELEGATE_STARTED = "delegate.started"
    DELEGATE_PROGRESS = "delegate.progress"
    DELEGATE_COMPLETED = "delegate.completed"
    DELEGATE_BLOCKED = "delegate.blocked"

    MCP_STARTED = "mcp.started"
    MCP_COMPLETED = "mcp.completed"
    MCP_FAILED = "mcp.failed"


# ---------------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------------


class RunStartedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    message_preview: str = ""  # first 100 chars of user message


class RunCompletedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    duration_ms: int = 0


class RunFailedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    error_class: str
    error_message: str
    duration_ms: int = 0


class PlanProposedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str
    step_count: int
    summary: str = ""


class AssistantDeltaPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    delta: str  # one text chunk emitted by the LLM


class AssistantFinalPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    content_length: int
    input_tokens: int = 0
    output_tokens: int = 0


class ToolStartedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    tool_name: str
    args_summary: str = ""  # sanitized, max 200 chars


class ToolCompletedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    tool_name: str
    latency_ms: int
    result_summary: str = ""  # sanitized, max 300 chars


class ToolFailedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    tool_name: str
    error_class: str
    error_message: str


class SkillStartedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    skill_name: str
    task_id: str


class SkillProgressPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    skill_name: str
    task_id: str
    message: str = ""


class SkillCompletedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    skill_name: str
    task_id: str
    duration_ms: int = 0


class SkillFailedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    skill_name: str
    task_id: str
    reason: str = ""


class DelegateStartedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    task_id: str
    batch_id: str = ""


class DelegateProgressPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    task_id: str
    message: str = ""


class DelegateCompletedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    task_id: str
    status: str = ""
    duration_ms: int = 0


class DelegateBlockedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    task_id: str
    reason: str = ""


class McpStartedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    server_id: str
    tool_name: str


class McpCompletedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    server_id: str
    tool_name: str
    latency_ms: int = 0


class McpFailedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    server_id: str
    tool_name: str
    error_class: str = ""


# ---------------------------------------------------------------------------
# Discriminated union of all payload types
# ---------------------------------------------------------------------------

AnyPayload = Annotated[
    RunStartedPayload
    | RunCompletedPayload
    | RunFailedPayload
    | PlanProposedPayload
    | AssistantDeltaPayload
    | AssistantFinalPayload
    | ToolStartedPayload
    | ToolCompletedPayload
    | ToolFailedPayload
    | SkillStartedPayload
    | SkillProgressPayload
    | SkillCompletedPayload
    | SkillFailedPayload
    | DelegateStartedPayload
    | DelegateProgressPayload
    | DelegateCompletedPayload
    | DelegateBlockedPayload
    | McpStartedPayload
    | McpCompletedPayload
    | McpFailedPayload,
    Field(),
]

# ---------------------------------------------------------------------------
# Main event envelope
# ---------------------------------------------------------------------------


class AgentRunEvent(BaseModel):
    """A single structured event emitted during an agent chat run.

    Attributes
    ----------
    run_id:
        UUID identifying the single request/response cycle.  Constant for
        all events within one ``process_chat_message_stream`` invocation.
    session_id:
        Conversation session identifier (from the HTTP layer).
    user_id:
        Authenticated user the run belongs to.
    event_seq:
        Monotonically increasing integer within a run, starting at 0.
    timestamp:
        UTC ISO-8601 timestamp when the event was created.
    event_type:
        One of the ``RunEventType`` string constants.
    payload:
        Typed payload model for the event type.
    """

    run_id: str
    session_id: str
    user_id: str
    event_seq: int
    timestamp: str
    event_type: str
    payload: AnyPayload


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_event(
    event_type: str,
    run_id: str,
    session_id: str,
    user_id: str,
    seq: int,
    payload: AnyPayload,
) -> AgentRunEvent:
    """Construct an ``AgentRunEvent`` with a current UTC timestamp.

    Parameters
    ----------
    event_type:
        One of the ``RunEventType`` string constants.
    run_id:
        UUID string for the run (shared across all events in one run).
    session_id:
        Conversation session identifier.
    user_id:
        Authenticated user identifier.
    seq:
        Monotonically increasing event sequence number (starts at 0).
    payload:
        Pre-constructed payload model.
    """
    return AgentRunEvent(
        run_id=run_id,
        session_id=session_id,
        user_id=user_id,
        event_seq=seq,
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        payload=payload,
    )


def new_run_id() -> str:
    """Generate a fresh run UUID."""
    return str(uuid.uuid4())
