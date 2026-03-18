"""graphclaw.gateway.models — Extended Pydantic models for the channel gateway.

Description
-----------
Provides additional Pydantic data transfer objects used by gateway routes and
task-matching logic.  This module supplements ``graphclaw.gateway.schemas`` with
models for task matching results, health status responses, and a unified
``EmailConfig`` value object for email channel configuration.

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
- EmailConfig: Configuration value object for email channel credentials.

Dependencies
------------
- pydantic: BaseModel, Field (third-party).
- graphclaw.models.enums: MatchedBy, ConfidenceLevel.

Notes
-----
``InboundMessage`` and ``OutboundMessage`` live in ``graphclaw.gateway.schemas``
to keep the core DTOs isolated from domain-level enumerations.  This module
extends the gateway model surface without requiring changes to that file.
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


class EmailConfig(BaseModel):
    """Configuration value object for the email channel.

    Collects all credentials and tuning parameters required by both the
    ``EmailPoller`` (IMAP) and the ``EmailSender`` (SMTP) components.  A single
    instance of this model is typically constructed from environment variables
    at application startup and injected into the email components.

    Attributes
    ----------
    imap_host:
        IMAP server hostname (e.g. ``"imap.gmail.com"``).  Empty string
        disables the IMAP poller.
    imap_port:
        IMAP server port.  Defaults to 993 (IMAP over TLS).
    smtp_host:
        SMTP server hostname (e.g. ``"smtp.gmail.com"``).
    smtp_port:
        SMTP server port.  Defaults to 587 (STARTTLS).
    username:
        Login username / email address for both IMAP and SMTP.
    password:
        App-specific password or OAuth token for IMAP/SMTP authentication.
    poll_interval:
        Seconds between IMAP poll cycles.  Defaults to 30.0.
    enabled:
        When ``False`` (the default), the email channel is disabled and neither
        the poller nor the sender will be started.
    """

    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    poll_interval: float = 30.0
    enabled: bool = False
