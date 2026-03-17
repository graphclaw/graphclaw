"""Base node model and ID generation utilities for GraphClaw."""

import re
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

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
    return f"USER-{uuid.uuid4()}"


def generate_goal_id() -> str:
    """Generate a goal ID in the form GOAL-{uuid}."""
    return f"GOAL-{uuid.uuid4()}"


def generate_constraint_id() -> str:
    """Generate a constraint ID in the form CON-{uuid}."""
    return f"CON-{uuid.uuid4()}"


def generate_resource_id() -> str:
    """Generate a resource ID in the form RES-{uuid}."""
    return f"RES-{uuid.uuid4()}"


def generate_edge_id() -> str:
    """Generate an edge ID in the form EDGE-{uuid}."""
    return f"EDGE-{uuid.uuid4()}"


def generate_checkin_node_id() -> str:
    """Generate a check-in node ID in the form CHK-{uuid}."""
    return f"CHK-{uuid.uuid4()}"


def utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validator helpers
# ---------------------------------------------------------------------------


def validate_task_id(v: str) -> str:
    if not TASK_ID_PATTERN.match(v):
        raise ValueError(
            f"Invalid task ID '{v}'. Expected format: TSK-<INITIALS>-<SEQ>-<TYPE_CODE>"
        )
    return v


def validate_user_id(v: str) -> str:
    if not USER_ID_PATTERN.match(v):
        raise ValueError(f"Invalid user ID '{v}'. Expected format: USER-<identifier>")
    return v


def validate_goal_id(v: str) -> str:
    if not GOAL_ID_PATTERN.match(v):
        raise ValueError(f"Invalid goal ID '{v}'. Expected format: GOAL-<identifier>")
    return v


def validate_constraint_id(v: str) -> str:
    if not CONSTRAINT_ID_PATTERN.match(v):
        raise ValueError(
            f"Invalid constraint ID '{v}'. Expected format: CON-<identifier>"
        )
    return v


def validate_resource_id(v: str) -> str:
    if not RESOURCE_ID_PATTERN.match(v):
        raise ValueError(
            f"Invalid resource ID '{v}'. Expected format: RES-<identifier>"
        )
    return v


def validate_edge_id(v: str) -> str:
    if not EDGE_ID_PATTERN.match(v):
        raise ValueError(f"Invalid edge ID '{v}'. Expected format: EDGE-<identifier>")
    return v


# ---------------------------------------------------------------------------
# Base node
# ---------------------------------------------------------------------------


class BaseNode(BaseModel):
    """Common fields for every graph node in the GraphClaw property graph."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
