"""graphclaw.infra.logging.events — PII-safe structured log event models.

These Pydantic models define explicit field allowlists that prevent accidental
logging of sensitive data (message bodies, user content, tool arguments, etc.).

Usage pattern:
    event = AgentMessageEvent(user_id=uid, input_tokens=100, output_tokens=50, latency_ms=300)
    logger.info("agent.message", extra={"event_type": "agent.message", **event.model_dump()})

The model validates; model_dump() produces only declared fields; extra={} carries them
into the LogRecord. The transport layer never sees raw dicts with arbitrary keys.
"""

from __future__ import annotations

from pydantic import BaseModel


class AgentToolCallEvent(BaseModel):
    """Log event for an agent tool invocation (no args/body allowed)."""

    tool_name: str
    user_id: str
    latency_ms: int


class AgentMessageEvent(BaseModel):
    """Log event for an agent LLM message (no content allowed)."""

    user_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class AgentScoringCycleEvent(BaseModel):
    """Log event for a scoring cycle completion."""

    user_id: str
    tasks_scored: int
    top_task_id: str | None
    queue_depth: int
    trigger_source: str


class InboundProcessedEvent(BaseModel):
    """Log event for an inbound message processing (no body/subject allowed)."""

    message_id: str
    channel: str
    task_id: str | None
    signal: str | None
    matched_by: str | None


class IntelligenceUpdateEvent(BaseModel):
    """Log event for intelligence layer update action (no text allowed)."""

    task_id: str | None
    channel: str
    direction: str
    action_taken: str


class OutboundSentEvent(BaseModel):
    """Log event for an outbound message sent (no body allowed)."""

    task_id: str | None
    channel: str
    recipient_hashed: str
    subject_length: int


class MCPActionEvent(BaseModel):
    """Audit log event for an MCP tool call (no args/response content)."""

    user_id: str
    server_id: str
    server_name: str
    tool_name: str
    success: bool
    latency_ms: int
    task_id: str | None = None


class AgentTaskStartedEvent(BaseModel):
    """Audit event: sub-agent picked up a delegated task."""

    agent_id: str
    task_id: str
    session_id: str
    parent_task_id: str | None = None
    batch_id: str = ""


class AgentTaskProgressEvent(BaseModel):
    """Audit event: sub-agent reported an intermediate progress update."""

    agent_id: str
    task_id: str
    session_id: str
    message: str
    iteration: int = 0


class AgentTaskCompletedEvent(BaseModel):
    """Audit event: sub-agent finished executing a delegated task."""

    agent_id: str
    task_id: str
    session_id: str
    status: str
    duration_ms: int
    parent_task_id: str | None = None
    batch_id: str = ""


class AgentTaskBlockedEvent(BaseModel):
    """Audit event: sub-agent encountered a blocker or heartbeat timeout."""

    agent_id: str
    task_id: str
    session_id: str
    reason: str


class AgentHeartbeatEvent(BaseModel):
    """Audit event: sub-agent liveness pulse."""

    agent_id: str
    task_id: str
    session_id: str
