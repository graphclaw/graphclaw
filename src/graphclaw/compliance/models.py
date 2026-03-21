"""graphclaw.compliance.models — Frozen dataclasses for compliance domain objects.

Description
-----------
Defines the immutable data models used throughout the compliance layer:
erasure requests, erasure lifecycle status, audit event records, and data
export manifests.  All models use frozen dataclasses so they are safe to
pass across async boundaries without mutation risk.

Design Patterns
---------------
- Frozen Dataclass: All models are ``frozen=True`` to prevent accidental
  mutation after creation, enforcing value-object semantics.
- Enum: ``ErasureStatus`` uses ``str`` mixin so values serialise cleanly to
  JSON without extra conversion steps.

Public API
----------
- ErasureRequest: Single right-to-erasure request from a user.
- ErasureStatus: Lifecycle states for an erasure request.
- AuditEvent: A single structured audit log entry.
- DataExport: Manifest for a completed user data export archive.

Dependencies
------------
- dataclasses: dataclass, field.
- datetime: datetime.
- enum: Enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ---------------------------------------------------------------------------
# ErasureRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErasureRequest:
    """A right-to-erasure request submitted by or on behalf of a user.

    Attributes
    ----------
    user_id:
        The ``USER-{uuid}`` identifier of the subject whose data is to be
        erased.
    requested_at:
        UTC timestamp when the request was created.
    requester_email:
        Email address of the person making the request (may differ from the
        subject's email when submitted by an admin or DPO).
    request_id:
        Unique identifier in the format ``ERASURE-{uuid4 hex[:12]}``.
    reason:
        Optional free-text reason provided by the requester.
    """

    user_id: str
    requested_at: datetime
    requester_email: str
    request_id: str  # format: ERASURE-{uuid4 hex[:12]}
    reason: str = ""


# ---------------------------------------------------------------------------
# ErasureStatus
# ---------------------------------------------------------------------------


class ErasureStatus(str, Enum):
    """Lifecycle states for a right-to-erasure request."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """A single structured audit log entry (PRD Section 32.3).

    Attributes
    ----------
    event_id:
        Unique identifier in the format ``AUDIT-{uuid4 hex[:12]}``.
    user_id:
        The ``USER-{uuid}`` identifier of the user this event is scoped to.
    action:
        Dot-namespaced action label, e.g. ``"task.created"``,
        ``"auth.login"``, ``"erasure.requested"``.
    resource_type:
        Graph node label of the affected resource, e.g. ``"TaskNode"``,
        ``"UserNode"``.
    resource_id:
        Identifier of the specific resource node.
    timestamp:
        UTC timestamp when the event occurred.
    ip_address:
        Optional originating IP address of the request.
    metadata:
        Arbitrary key-value pairs for additional context.  Sensitive values
        must be scrubbed before this field is populated.
    """

    event_id: str  # format: AUDIT-{uuid4 hex[:12]}
    user_id: str
    action: str  # e.g. "task.created", "auth.login", "erasure.requested"
    resource_type: str  # e.g. "TaskNode", "UserNode"
    resource_id: str
    timestamp: datetime
    ip_address: str | None = None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DataExport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataExport:
    """Manifest for a completed user data export archive.

    Attributes
    ----------
    user_id:
        The ``USER-{uuid}`` identifier of the user whose data was exported.
    export_id:
        Unique identifier in the format ``EXPORT-{uuid4 hex[:12]}``.
    created_at:
        UTC timestamp when the export was generated.
    storage_key:
        S3 object key where the export JSON is stored.
    expires_at:
        UTC timestamp when the export object will be deleted (7 days after
        ``created_at``).
    record_count:
        Total number of records included in the export.
    """

    user_id: str
    export_id: str  # format: EXPORT-{uuid4 hex[:12]}
    created_at: datetime
    storage_key: str  # S3 key where export is stored
    expires_at: datetime  # 7 days after creation
    record_count: int = 0


__all__ = [
    "ErasureRequest",
    "ErasureStatus",
    "AuditEvent",
    "DataExport",
]
