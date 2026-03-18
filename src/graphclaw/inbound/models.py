"""graphclaw.inbound.models — Domain models for the Inbound Update Protocol.

Description
-----------
Defines the typed value objects that flow through the inbound processing
pipeline: ``StatusSignal`` captures the inferred intent of an inbound
message, ``TaskResolution`` records how (or whether) the message was
matched to a graph task, ``StatusExtraction`` holds the extracted signal
and suggested state transition, and ``InboundResult`` is the complete
output of a single message's processing run.

Design Patterns
---------------
- Value Objects: All models are immutable Pydantic BaseModels carrying data
  without behaviour; business logic lives in resolver.py, extractor.py, and
  processor.py.
- String Enums: ``StatusSignal`` inherits from ``str`` so values serialise to
  plain JSON strings without extra conversion, consistent with the rest of the
  GraphClaw enum convention.

Public API
----------
- StatusSignal: Enum of extractable status signals (DONE, IN_PROGRESS, BLOCKED,
  DELAYED, NEEDS_HELP, INFO_ONLY, UNKNOWN).
- TaskResolution: Result of resolving an inbound message to a graph task.
- StatusExtraction: Extracted status information from message text.
- InboundResult: Complete result of processing a single inbound message.

Dependencies
------------
- pydantic: BaseModel.
- enum: Enum.
- graphclaw.models.enums: MatchedBy, ConfidenceLevel, TaskState.

Notes
-----
``StatusSignal`` is defined here (not in ``graphclaw.models.enums``) because
it belongs to the inbound subsystem's domain and is not used elsewhere in the
core graph model. Callers should import it from ``graphclaw.inbound`` or
``graphclaw.inbound.models``.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from graphclaw.models.enums import ConfidenceLevel, MatchedBy, TaskState


class StatusSignal(str, Enum):
    """Extracted status signal from an inbound message.

    Represents the most likely intent conveyed by the message body after
    keyword matching. ``UNKNOWN`` is the default when no keywords match;
    ``INFO_ONLY`` is used when the message has content but no specific
    status keywords were detected.
    """

    DONE = "DONE"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DELAYED = "DELAYED"
    NEEDS_HELP = "NEEDS_HELP"
    INFO_ONLY = "INFO_ONLY"
    UNKNOWN = "UNKNOWN"


class TaskResolution(BaseModel):
    """Result of resolving an inbound message to a graph task.

    Attributes
    ----------
    task_id:
        The resolved task identifier, or ``None`` if no match was found.
    matched_by:
        How the match was determined: ``TASK_ID`` for regex extraction,
        ``VECTOR_SEARCH`` for embedding-based nearest-neighbour lookup.
        ``None`` when no match was found.
    confidence:
        Categorical confidence level for this resolution.
    score:
        Numeric confidence score in the range 0.0–1.0. ``1.0`` for exact
        task ID matches verified against the database; lower for unverified
        or vector-search matches.
    matched_text:
        The raw text fragment that triggered the match (the task ID string
        for ID matches, or the task title for vector matches).
    """

    task_id: str | None = None
    matched_by: MatchedBy | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    score: float = 0.0
    matched_text: str = ""


class StatusExtraction(BaseModel):
    """Extracted status information from message text.

    Attributes
    ----------
    signal:
        The dominant status signal detected in the message.
    confidence:
        Categorical confidence for the extracted signal, derived from the
        number of matching keywords found.
    summary:
        A brief excerpt from the message (first sentence, max 100 chars)
        used as a human-readable description of the update.
    suggested_state:
        The ``TaskState`` to transition to, if the signal implies a state
        change. ``None`` for ``INFO_ONLY`` and ``UNKNOWN`` signals.
    """

    signal: StatusSignal = StatusSignal.UNKNOWN
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    summary: str = ""
    suggested_state: TaskState | None = None


class InboundResult(BaseModel):
    """Complete result of processing a single inbound message.

    Attributes
    ----------
    message_id:
        The identifier of the original inbound message.
    session_id:
        Distributed tracing session identifier propagated from the inbound
        message through the full processing pipeline.
    resolution:
        The task resolution outcome (matched task, method, confidence).
    status:
        The extracted status signal and suggested state transition.
    action_taken:
        Short label describing the action the processor took, e.g.
        ``"state_update_published"``, ``"unmatched"``, ``"no_action"``.
    followup_needed:
        ``True`` when the processor determines human routing or follow-up
        is required (unmatched messages, BLOCKED/NEEDS_HELP signals).
    """

    message_id: str
    session_id: str
    resolution: TaskResolution
    status: StatusExtraction
    action_taken: str = ""
    followup_needed: bool = False
