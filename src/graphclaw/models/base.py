"""graphclaw.models.base — Base node model and typed ID generation utilities.

Description
-----------
Defines ``BaseNode``, the Pydantic base class shared by every graph node type,
along with the ID format constants, generator functions, and validator helpers
that enforce the GraphClaw node ID naming conventions (e.g. ``TSK-AB-1234-ATM``).
This module is the single source of truth for ID patterns so that both node
models and the DB layer agree on what constitutes a valid identifier.

Design Patterns
---------------
- Base Class: ``BaseNode`` carries ``id``, ``created_at``, ``updated_at``, and
  ``version`` as the minimal shared contract for all graph vertices.
- Factory Functions: ``generate_task_id``, ``generate_user_id``, etc. produce
  validated IDs so callers never construct raw strings.

Public API
----------
- BaseNode: Abstract base Pydantic model for all graph nodes.
- TASK_ID_PATTERN: Compiled regex for task ID validation.
- USER_ID_PATTERN: Compiled regex for user ID validation.
- GOAL_ID_PATTERN: Compiled regex for goal ID validation.
- CONSTRAINT_ID_PATTERN: Compiled regex for constraint ID validation.
- RESOURCE_ID_PATTERN: Compiled regex for resource ID validation.
- EDGE_ID_PATTERN: Compiled regex for edge ID validation.
- CHECKIN_NODE_ID_PATTERN: Compiled regex for check-in node ID validation.
- HANDOFF_NODE_ID_PATTERN: Compiled regex for handoff node ID validation.
- GRANT_ID_PATTERN: Compiled regex for visibility grant ID validation.
- MCP_SERVER_ID_PATTERN: Compiled regex for MCP server ID validation.
- generate_id: Generic ``{PREFIX}-{uuid}`` ID generator.
- generate_task_id: Generate a task ID in TSK-{INITIALS}-{SEQ}-{TYPE} format.
- generate_user_id: Generate a USER-{uuid} ID.
- generate_goal_id: Generate a GOAL-{uuid} ID.
- generate_constraint_id: Generate a CON-{uuid} ID.
- generate_resource_id: Generate a RES-{uuid} ID.
- generate_edge_id: Generate an EDGE-{uuid} ID.
- generate_checkin_node_id: Generate a CHK-{uuid} ID.
- generate_handoff_node_id: Generate a HND-{uuid} ID.
- generate_org_id: Generate an ORG-{uuid} ID.
- generate_workspace_id: Generate a WS-{uuid} ID.
- generate_grant_id: Generate a GRANT-{uuid} ID.
- generate_mcp_server_id: Generate an MCP-{short_uuid} ID.
- utcnow: Return the current timezone.utc datetime (timezone-aware).
- validate_id: Generic ID validator (pattern + entity name).
- validate_task_id / validate_*_id: Thin wrappers around validate_id for Pydantic field_validator use.
- validate_handoff_id: Thin wrapper for handoff node ID validation.
- validate_grant_id: Thin wrapper for visibility grant ID validation.
- validate_mcp_server_id: Thin wrapper for MCP server ID validation.

Dependencies
------------
- pydantic: BaseModel, ConfigDict, Field, field_validator.
- graphclaw.models.enums: TaskType (for the task type → code mapping).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from graphclaw.models.enums import TaskType

# ---------------------------------------------------------------------------
# ID pattern constants
# ---------------------------------------------------------------------------

TASK_ID_PATTERN = re.compile(
    r"^TSK-[A-Z]{2,}-\d{4,}-(?:DEL|ATM|FLW|CMP|APR|MIL|RVW|REC|DEC|CHK|RES)$"
)
USER_ID_PATTERN = re.compile(r"^USER-[\w-]+$")
GOAL_ID_PATTERN = re.compile(r"^GOAL-[\w-]+$")
CONSTRAINT_ID_PATTERN = re.compile(r"^CON-[\w-]+$")
RESOURCE_ID_PATTERN = re.compile(r"^RES-[\w-]+$")
EDGE_ID_PATTERN = re.compile(r"^EDGE-[\w-]+$")
CHECKIN_NODE_ID_PATTERN = re.compile(r"^CHK-[\w-]+$")
HANDOFF_NODE_ID_PATTERN = re.compile(r"^HND-[\w-]+$")
ORG_ID_PATTERN = re.compile(r"^ORG-[\w-]+$")
WORKSPACE_ID_PATTERN = re.compile(r"^WS-[\w-]+$")
GRANT_ID_PATTERN = re.compile(r"^GRANT-[\w-]+$")
MCP_SERVER_ID_PATTERN = re.compile(r"^MCP-[\w-]+$")

# ---------------------------------------------------------------------------
# Task type → type-code mapping
# ---------------------------------------------------------------------------

_TASK_TYPE_CODE: dict[TaskType, str] = {
    TaskType.DELEGATED: "DEL",
    TaskType.ATOMIC: "ATM",
    TaskType.FOLLOWUP: "FLW",
    TaskType.COMPOSITE: "CMP",
    TaskType.APPROVAL: "APR",
    TaskType.MILESTONE: "MIL",
    TaskType.REVIEW: "RVW",
    TaskType.RECURRING: "REC",
    TaskType.DECISION: "DEC",
    TaskType.CHECKIN: "CHK",
    TaskType.RESEARCH: "RES",
}

# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------


def _short_uuid() -> str:
    """Return an 8-character upper-case hex fragment of a UUID4."""
    return uuid.uuid4().hex[:8].upper()


def _sequence_number() -> str:
    """Return a zero-padded 4-digit sequence number using the last 4 hex digits of a UUID4."""
    return str(int(uuid.uuid4().hex[:4], 16)).zfill(4)


def generate_id(prefix: str) -> str:
    """Generate a generic ``{PREFIX}-{uuid}`` ID.

    Args:
        prefix: The uppercase prefix string (e.g. ``"USER"``, ``"GOAL"``).

    Returns:
        A string of the form ``{PREFIX}-{uuid4}``.
    """
    return f"{prefix}-{uuid.uuid4()}"


def generate_task_id(user_initials: str, task_type: TaskType) -> str:
    """Generate a task ID in the form TSK-{INITIALS}-{SEQUENCE}-{TYPE_CODE}.

    Args:
        user_initials: 2+ uppercase letters identifying the owning user.
        task_type: The TaskType enum value for this task.

    Returns:
        A string matching TASK_ID_PATTERN.

    Example:
        >>> generate_task_id("AB", TaskType.ATOMIC)
        "TSK-AB-1234-ATM"
    """
    initials = user_initials.upper()
    seq = _sequence_number()
    code = _TASK_TYPE_CODE[task_type]
    return f"TSK-{initials}-{seq}-{code}"


def generate_user_id() -> str:
    """Generate a user ID in the form USER-{uuid}."""
    return generate_id("USER")


def generate_goal_id() -> str:
    """Generate a goal ID in the form GOAL-{uuid}."""
    return generate_id("GOAL")


def generate_constraint_id() -> str:
    """Generate a constraint ID in the form CON-{uuid}."""
    return generate_id("CON")


def generate_resource_id() -> str:
    """Generate a resource ID in the form RES-{uuid}."""
    return generate_id("RES")


def generate_edge_id() -> str:
    """Generate an edge ID in the form EDGE-{uuid}."""
    return generate_id("EDGE")


def generate_checkin_node_id() -> str:
    """Generate a check-in node ID in the form CHK-{uuid}."""
    return generate_id("CHK")


def generate_handoff_node_id() -> str:
    """Generate a handoff node ID in the form HND-{uuid}."""
    return generate_id("HND")


def generate_org_id() -> str:
    """Generate an organization ID in the form ORG-{uuid}."""
    return generate_id("ORG")


def generate_workspace_id() -> str:
    """Generate a workspace ID in the form WS-{uuid}."""
    return generate_id("WS")


def generate_grant_id() -> str:
    """Generate a visibility grant ID in the form GRANT-{uuid}."""
    return generate_id("GRANT")


def generate_mcp_server_id() -> str:
    """Generate an MCP server ID in the form MCP-{short_uuid}.

    Returns:
        A string matching MCP_SERVER_ID_PATTERN, e.g. ``MCP-A1B2C3D4``.
    """
    return f"MCP-{_short_uuid()}"


def utcnow() -> datetime:
    """Return the current timezone.utc datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validator helpers
# ---------------------------------------------------------------------------


def validate_id(v: str, pattern: re.Pattern, entity_name: str) -> str:
    """Generic ID validator used by all entity-specific validators.

    Args:
        v: The raw ID string to validate.
        pattern: Compiled regex the ID must match.
        entity_name: Human-readable entity label used in the error message
            (e.g. ``"task"``, ``"user"``).

    Returns:
        ``v`` unchanged if it matches *pattern*.

    Raises:
        ValueError: If ``v`` does not match *pattern*.
    """
    if not pattern.match(v):
        prefix = pattern.pattern.split("-")[0].lstrip("^")
        raise ValueError(f"Invalid {entity_name} ID '{v}'. Expected format: {prefix}-<identifier>")
    return v


def validate_task_id(v: str) -> str:
    if not TASK_ID_PATTERN.match(v):
        raise ValueError(
            f"Invalid task ID '{v}'. Expected format: TSK-<INITIALS>-<SEQ>-<TYPE_CODE>"
        )
    return v


def validate_user_id(v: str) -> str:
    return validate_id(v, USER_ID_PATTERN, "user")


def validate_goal_id(v: str) -> str:
    return validate_id(v, GOAL_ID_PATTERN, "goal")


def validate_constraint_id(v: str) -> str:
    return validate_id(v, CONSTRAINT_ID_PATTERN, "constraint")


def validate_resource_id(v: str) -> str:
    return validate_id(v, RESOURCE_ID_PATTERN, "resource")


def validate_edge_id(v: str) -> str:
    return validate_id(v, EDGE_ID_PATTERN, "edge")


def validate_org_id(v: str) -> str:
    return validate_id(v, ORG_ID_PATTERN, "organization")


def validate_workspace_id(v: str) -> str:
    return validate_id(v, WORKSPACE_ID_PATTERN, "workspace")


def validate_handoff_id(v: str) -> str:
    return validate_id(v, HANDOFF_NODE_ID_PATTERN, "handoff")


def validate_grant_id(v: str) -> str:
    if not GRANT_ID_PATTERN.match(v):
        raise ValueError(f"Invalid grant ID format: {v!r} (expected GRANT-...)")
    return v


def validate_mcp_server_id(v: str) -> str:
    if not MCP_SERVER_ID_PATTERN.match(v):
        raise ValueError(f"Invalid MCP server ID format: {v!r} (expected MCP-...)")
    return v


# ---------------------------------------------------------------------------
# Base node
# ---------------------------------------------------------------------------


class BaseNode(BaseModel):
    """Common fields for every graph node in the GraphClaw property graph.

    Wave 0 lifecycle fields (FR-DEL-002, FR-DEL-003, FR-DEL-007):
    These fields are write-restricted to admin_principal at the DB schema level.
    Agents MUST NOT update these directly; use the archive_* tool set instead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    version: int = Field(
        default=0,
        description=(
            "Optimistic locking version counter. Increment on every write; "
            "reject writes where version in DB != version in payload."
        ),
    )

    # ------------------------------------------------------------------
    # Wave 0: Lifecycle fields (admin_principal-only writes)
    # ------------------------------------------------------------------
    archived_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp when this node was archived. NULL means not archived. "
            "Write-restricted to admin_principal via Postgres trigger."
        ),
    )
    archived_by: str | None = Field(
        default=None,
        description="User ID of the person who requested archival.",
    )
    archive_reason: str | None = Field(
        default=None,
        description="Human-readable reason for archival.",
    )
    purge_after: datetime | None = Field(
        default=None,
        description=(
            "Timestamp after which the purge worker may hard-delete this node. "
            "Set by the archive flow; default 24h after archived_at. "
            "Write-restricted to admin_principal."
        ),
    )
    purge_cancelled_at: datetime | None = Field(
        default=None,
        description=(
            "Set when user cancels a pending purge. Purge worker skips rows "
            "where this field is NOT NULL."
        ),
    )
    # Legal hold fields — prevent purge even after purge_after elapsed.
    legal_hold: bool = Field(
        default=False,
        description=(
            "When True, the purge worker skips this node regardless of purge_after. "
            "Set/released only by admin_principal."
        ),
    )
    hold_reason: str | None = Field(default=None)
    hold_set_by: str | None = Field(default=None)
    hold_set_at: datetime | None = Field(default=None)
    # link_status: for TombstoneNode tracking (set when node is redirected).
    link_status: str | None = Field(
        default=None,
        description=(
            "Set to 'redirected' when a TombstoneNode replaces this node. "
            "Write-restricted to admin_principal."
        ),
    )
