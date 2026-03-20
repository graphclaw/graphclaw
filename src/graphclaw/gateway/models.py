"""graphclaw.gateway.models — Extended Pydantic models for the channel gateway.

Description
-----------
Provides additional Pydantic data transfer objects used by gateway routes and
task-matching logic.  This module supplements ``graphclaw.gateway.schemas`` with
models for task matching results and health status responses.

Design Patterns
---------------
- DTO / Value Object: All models are immutable, schema-validated containers.
- Pydantic v2: Uses ``model_validator`` and ``Field`` for validation with defaults.
- Composition: Imports ``MatchedBy`` and ``ConfidenceLevel`` from the canonical
  domain enums so gateway code shares the same vocabulary as the graph layer.

Public API
----------
- TaskMatch: Result of matching an inbound message to a task node.
- HealthStatus: Health / readiness check response payload.
- EmailConfig: Configuration value object for email channel credentials (re-exported).

Dependencies
------------
- pydantic: BaseModel, Field (third-party).
- graphclaw.models.enums: MatchedBy, ConfidenceLevel.
- graphclaw.gateway.channels.email.config: EmailConfig (backward compat).

Notes
-----
``InboundMessage`` and ``OutboundMessage`` live in ``graphclaw.gateway.schemas``
to keep the core DTOs isolated from domain-level enumerations.  This module
extends the gateway model surface without requiring changes to that file.

``EmailConfig`` has moved to ``graphclaw.gateway.channels.email.config`` and is
re-exported here for backward compatibility.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from graphclaw.models.enums import ConfidenceLevel, MatchedBy


class TaskMatch(BaseModel):
    """Result of matching an inbound message to a task node.

    Attributes
    ----------
    task_id:
        Graph node ID of the matched task (e.g. ``"TSK-AB-0001-ATM"``).
    matched_by:
        Strategy used to produce the match (``TASK_ID`` or ``VECTOR_SEARCH``).
    confidence:
        Confidence tier of the match (``HIGH``, ``MEDIUM``, or ``LOW``).
    matched_text:
        Excerpt of the message text that triggered the match.  Empty string
        when the match was made by explicit task ID reference.
    """

    task_id: str
    matched_by: MatchedBy
    confidence: ConfidenceLevel
    matched_text: str = ""


class HealthStatus(BaseModel):
    """Health check response payload.

    Attributes
    ----------
    status:
        Overall service status string.  Typically ``"ok"``, ``"ready"``,
        ``"degraded"``, or ``"unavailable"``.
    version:
        Semantic version string of the gateway service.
    services:
        Mapping of downstream service names to their individual status strings.
        Example: ``{"db": "ok", "redis": "ok"}``.
    """

    status: str = "ok"
    version: str = "0.1.0"
    services: dict[str, str] = Field(default_factory=dict)


# Backward compatibility — EmailConfig moved to channels.email.config
from graphclaw.gateway.channels.email.config import EmailConfig  # noqa: F401, E402
