"""graphclaw.models.enums — All domain enumerations for the GraphClaw system.

Description
-----------
Centralises every ``str`` + ``Enum`` used across the GraphClaw domain model.
Using ``str`` mixins allows direct serialisation to/from JSON without extra
conversion and ensures enum values can be stored as plain strings in the AGE
property graph.

Design Patterns
---------------
- String Enums: All enums inherit from ``str`` so that ``enum.value`` equals
  the string representation used in the database and API.

Public API
----------
- TaskType: The 11 task variant types (ATOMIC, COMPOSITE, DELEGATED, etc.).
- TaskState: All valid task states in the state machine.
- EdgeType: All directed edge relationship labels.
- GateType: AND / OR completion gate for composite tasks and DEPENDS_ON edges.
- GoalPriority: P1 / P2 / P3 goal priority tiers.
- ConstraintType: Categories of constraint (DEADLINE, BUDGET, etc.).
- ResourceType: HUMAN or AI_AGENT resource classification.
- AutonomyLevel: SUGGEST / AUTONOMOUS / REQUIRE_APPROVAL agent permission levels.
- ChangedBy: Who triggered a state change (AGENT, HUMAN, SYSTEM, CASCADE).
- GoalState: Active lifecycle states for GoalNode.
- GoalOrigin: Whether a goal was user-defined or agent-inferred.
- CheckinState: Lifecycle states for CheckinNode messages.
- AvailabilityStatus: Resource availability signal levels.
- RiskLevel: LOW / MEDIUM / HIGH risk classification.
- ConstraintScope: Which entity type a constraint applies to.
- ConfidenceLevel: HIGH / MEDIUM / LOW confidence on task progress estimates.
- CompletionSignal: How a completion was determined (EXPLICIT, INFERRED, CASCADED).
- OverrideType: Human priority override actions (PRIORITIZE, DEPRIORITIZE, SNOOZE).
- MatchedBy: How an inbound update was matched to a task.
- BreakdownStrategy: SEQUENTIAL / PARALLEL / HYBRID sub-task ordering.
- EdgeStrength: HARD / SOFT edge strength for BLOCKS relationships.
- EdgeCreatedBy: Whether an edge was created by HUMAN or AGENT.
- VisibilityScope: Access level granted to a user on a specific node (VIEWER, EDITOR, OWNER).

Dependencies
------------
- enum: Standard library Enum base class.
"""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    ATOMIC = "ATOMIC"
    COMPOSITE = "COMPOSITE"
    DELEGATED = "DELEGATED"
    FOLLOWUP = "FOLLOWUP"
    APPROVAL = "APPROVAL"
    MILESTONE = "MILESTONE"
    REVIEW = "REVIEW"
    RECURRING = "RECURRING"
    DECISION = "DECISION"
    CHECKIN = "CHECKIN"
    RESEARCH = "RESEARCH"


class TaskState(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DELAYED = "DELAYED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    SNOOZED = "SNOOZED"
    INACTIVE_PENDING = "INACTIVE_PENDING"


class EdgeType(str, Enum):
    DEPENDS_ON = "DEPENDS_ON"
    SPAWNED_FROM = "SPAWNED_FROM"
    FOLLOW_UP_FOR = "FOLLOW_UP_FOR"
    BLOCKS = "BLOCKS"
    ASSIGNED_TO = "ASSIGNED_TO"
    OWNED_BY = "OWNED_BY"
    APPLIES_TO = "APPLIES_TO"
    PART_OF = "PART_OF"
    INFORMS = "INFORMS"
    BRANCHED_FROM = "BRANCHED_FROM"
    BATCHED_IN = "BATCHED_IN"
    REFERRED_BY = "REFERRED_BY"
    MEMBER_OF = "MEMBER_OF"  # UserNode → OrganizationNode / WorkspaceNode
    ADMIN_OF = "ADMIN_OF"  # UserNode → OrganizationNode / WorkspaceNode
    BELONGS_TO_ORG = "BELONGS_TO_ORG"  # WorkspaceNode → OrganizationNode
    SCOPED_TO_WS = "SCOPED_TO_WS"  # TaskNode / GoalNode → WorkspaceNode
    GRANTS_ACCESS_TO = "GRANTS_ACCESS_TO"  # VisibilityGrantNode → target node


class GateType(str, Enum):
    AND = "AND"
    OR = "OR"


class GoalPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ConstraintType(str, Enum):
    DEADLINE = "DEADLINE"
    BUDGET = "BUDGET"
    COMPLIANCE = "COMPLIANCE"
    EXTERNAL = "EXTERNAL"
    DEPENDENCY = "DEPENDENCY"
    CAPACITY = "CAPACITY"
    CUSTOM = "CUSTOM"


class ResourceType(str, Enum):
    HUMAN = "HUMAN"
    AI_AGENT = "AI_AGENT"


class AutonomyLevel(str, Enum):
    SUGGEST = "SUGGEST"
    AUTONOMOUS = "AUTONOMOUS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ChangedBy(str, Enum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    CASCADE = "CASCADE"


class GoalState(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    OBSOLETE = "OBSOLETE"
    ON_HOLD = "ON_HOLD"


class GoalOrigin(str, Enum):
    USER_DEFINED = "USER_DEFINED"
    AGENT_INFERRED = "AGENT_INFERRED"


class CheckinState(str, Enum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    RESPONDED = "RESPONDED"
    EXPIRED = "EXPIRED"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    AT_CAPACITY = "AT_CAPACITY"
    UNAVAILABLE = "UNAVAILABLE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConstraintScope(str, Enum):
    TASK = "TASK"
    MILESTONE = "MILESTONE"
    GOAL = "GOAL"
    GLOBAL = "GLOBAL"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CompletionSignal(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    CASCADED = "CASCADED"


class OverrideType(str, Enum):
    PRIORITIZE = "PRIORITIZE"
    DEPRIORITIZE = "DEPRIORITIZE"
    SNOOZE = "SNOOZE"


class MatchedBy(str, Enum):
    TASK_ID = "TASK_ID"
    VECTOR_SEARCH = "VECTOR_SEARCH"


class BreakdownStrategy(str, Enum):
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    HYBRID = "HYBRID"


class EdgeStrength(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class EdgeCreatedBy(str, Enum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"


# ---------------------------------------------------------------------------
# Phase 2 — Organizations & Workspaces
# ---------------------------------------------------------------------------


class OrgRole(str, Enum):
    """Role a user holds within an organization."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    GUEST = "GUEST"


class MembershipStatus(str, Enum):
    """Status of a user's membership in an org or workspace."""

    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class WorkspaceVisibility(str, Enum):
    """Who can see tasks/goals within a workspace."""

    PRIVATE = "PRIVATE"  # Only explicit members
    INTERNAL = "INTERNAL"  # All org members
    PUBLIC = "PUBLIC"  # Readable by any authenticated user


class VisibilityScope(str, Enum):
    """Access level granted to a user on a specific node."""

    VIEWER = "VIEWER"  # read-only
    EDITOR = "EDITOR"  # can update node properties and state
    OWNER = "OWNER"  # full control including revoking grants


# ---------------------------------------------------------------------------
# Phase 4 — MCP Server Integration
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Trust tier for a registered MCP server.

    AUTO    — tools from this server are called without user confirmation.
              Suitable for read-only tools the user has explicitly trusted.
    GATED   — the orchestrating agent proposes the tool call and waits for
              user approval before executing. Suitable for write operations.
    BLOCKED — server is registered but all tool calls are rejected. Used to
              temporarily suspend a server without removing its configuration.
    """

    AUTO = "AUTO"
    GATED = "GATED"
    BLOCKED = "BLOCKED"


class MCPTransport(str, Enum):
    """Transport protocol for a registered MCP server."""

    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


# ---------------------------------------------------------------------------
# Wave 1 — Identity, Tenancy Schema
# ---------------------------------------------------------------------------


class LinkStatus(str, Enum):
    """Status of a ResourceNode's link to a UserNode (FR-GRAPH-003)."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DETACHED_USER_ARCHIVED = "detached_user_archived"
    DETACHED_USER_PURGED = "detached_user_purged"


class DiscoverabilityLevel(str, Enum):
    """How discoverable a user is within their org directory (FR-GRAPH-005)."""

    ORG_DEFAULT = "org_default"
    DISCOVERABLE = "discoverable"
    NAME_ONLY = "name_only"
    HIDDEN = "hidden"


class OrgDirectoryVisibility(str, Enum):
    """Org-wide default for cross-user directory visibility (FR-GRAPH-006)."""

    OPEN = "open"
    NAME_ONLY = "name-only"
    CONSENT_REQUIRED = "consent-required"
    INVITATION_ONLY = "invitation-only"
