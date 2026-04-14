"""graphclaw.models.nodes — Pydantic node models for all graph vertex types.

Description
-----------
Defines the graph node types that form the GraphClaw property graph: TaskNode,
UserNode, GoalNode, ConstraintNode, ResourceNode, CheckinNode, OrganizationNode,
WorkspaceNode, and VisibilityGrantNode.  Every type inherits from ``BaseNode``
(id, created_at, updated_at, version) and uses field validators to enforce the
naming conventions defined in ``graphclaw.models.base``.  ``TaskNode`` is the
richest model, embedding sub-models for timeline, scoring, state history,
progress, overrides, autonomy, and type-specific metadata.

Design Patterns
---------------
- Pydantic v2 Models: All node types use ``BaseModel`` / ``BaseNode`` with
  ``field_validator`` for ID format enforcement.
- Discriminated Union: ``TaskNode.type_metadata`` uses ``TypeMetadata``
  (a ``Union`` discriminated on ``task_type``) to carry per-variant fields
  without requiring separate node classes per task type.
- Sub-models: Rich nested models (ScoringBlock, Timeline, OverrideBlock, etc.)
  keep TaskNode fields logically grouped and independently serialisable.

Public API
----------
- TaskNode: Core task vertex with 11 task type variants.
- UserNode: Human user who owns tasks and goals.
- GoalNode: High-level goal that tasks belong to.
- ConstraintNode: Business constraint governing tasks, milestones, or goals.
- ResourceNode: Any entity (human or AI agent) that can be assigned tasks.
- CheckinNode: Batched communication artifact sent to a resource.
- OrganizationNode: Multi-user organization workspace boundary.
- WorkspaceNode: Scoped collection of tasks and goals within an org.
- VisibilityGrantNode: Fine-grained access grant for a specific node and user.
- MCPServerNode: Registered MCP server for a user, storing transport config and trust tier.
- Timeline, ScoringBlock, StateHistoryEntry, ProgressBlock, OverrideBlock,
  AutonomyBlock, UpdateLogEntry, EmbeddingInputs: TaskNode sub-models.
- ScoringWeights, AutonomyDefaults, UserPreferences, BehavioralModel,
  WorkingHours: UserNode sub-models.

Dependencies
------------
- graphclaw.models.base: BaseNode, ID patterns, and validator helpers.
- graphclaw.models.enums: All domain enumerations.
- graphclaw.models.type_metadata: TypeMetadata discriminated union.
- pydantic: BaseModel, Field, field_validator.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from graphclaw.models.base import (
    CONSTRAINT_ID_PATTERN,
    GOAL_ID_PATTERN,
    MCP_SERVER_ID_PATTERN,
    ORG_ID_PATTERN,
    RESOURCE_ID_PATTERN,
    TASK_ID_PATTERN,
    USER_ID_PATTERN,
    WORKSPACE_ID_PATTERN,
    BaseNode,
)
from graphclaw.models.enums import (
    AutonomyLevel,
    AvailabilityStatus,
    ChangedBy,
    CheckinState,
    CompletionSignal,
    ConfidenceLevel,
    ConstraintScope,
    ConstraintType,
    GoalOrigin,
    GoalPriority,
    GoalState,
    MatchedBy,
    MCPTransport,
    MembershipStatus,
    OrgRole,
    OverrideType,
    ResourceType,
    RiskLevel,
    TaskState,
    TaskType,
    TrustTier,
    VisibilityScope,
    WorkspaceVisibility,
)
from graphclaw.models.type_metadata import TypeMetadata

# ---------------------------------------------------------------------------
# Sub-models shared by TaskNode
# ---------------------------------------------------------------------------


class Timeline(BaseModel):
    """Timeline block within a TaskNode."""

    deadline: datetime | None = None
    started_at: datetime | None = None
    estimated_effort_hours: float | None = None
    estimated_effort_days: float | None = None
    actual_effort_days: float | None = None
    completed_at: datetime | None = None


class ScoringBlock(BaseModel):
    """Stores the 7 raw scoring factor values for a TaskNode."""

    timeline_urgency: float = 0.0  # W1: 0.0 – 1.2
    dependency_weight: float = 0.0  # W2
    critical_path: float = 0.0  # W3: 0.0 or 1.0
    blocker: float = 0.0  # W4: 0.0, 0.6, or 1.0
    human_override: float = 0.0  # W5: -0.3 to +1.0
    resource_risk: float = 0.0  # W6: 0.0 – 1.0
    constraint_pressure: float = 0.0  # W7: 0.0 – 1.0
    computed_priority: float = 0.0  # final weighted score
    chain_urgency_rollup: float = 0.0
    last_scored_at: datetime | None = None
    score_reasoning: str | None = None


class StateHistoryEntry(BaseModel):
    """Single state-transition record, appended on every transition."""

    from_state: TaskState
    to_state: TaskState
    changed_at: datetime
    changed_by: ChangedBy
    reason: str | None = None

    @field_validator("from_state", "to_state", mode="before")
    @classmethod
    def _normalise_task_state(cls, v: object) -> object:
        _legacy = {
            "open": "PENDING",
            "in_progress": "IN_PROGRESS",
            "blocked": "BLOCKED",
            "complete": "COMPLETE",
            "cancelled": "CANCELLED",
            "snoozed": "SNOOZED",
            "active": "ACTIVE",
            "delayed": "DELAYED",
            "needs_review": "NEEDS_REVIEW",
            "inactive_pending": "INACTIVE_PENDING",
        }
        if isinstance(v, str):
            return _legacy.get(v, v)
        return v

    @field_validator("changed_by", mode="before")
    @classmethod
    def _normalise_changed_by(cls, v: object) -> object:
        """Map a raw user/agent ID string to the closest ChangedBy enum value."""
        if isinstance(v, str) and v not in ("AGENT", "HUMAN", "SYSTEM", "CASCADE"):
            if v.startswith("USER-"):
                return "HUMAN"
            return "AGENT"
        return v


class ProgressBlock(BaseModel):
    """Task progress tracking sub-model."""

    percentage: float = 0.0
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    last_update: datetime | None = None
    completion_signal: CompletionSignal = CompletionSignal.EXPLICIT


class OverrideBlock(BaseModel):
    """Human-override sub-model for a TaskNode."""

    is_overridden: bool = False
    override_type: OverrideType | None = None
    override_note: str | None = None
    set_by: str | None = None  # user_id
    set_at: datetime | None = None
    expires_at: datetime | None = None


class AutonomyBlock(BaseModel):
    """Per-node autonomy permission overrides."""

    auto_update_allowed: bool = False
    auto_close_allowed: bool = False
    requires_approval_from: str | None = None  # user_id; null = fully autonomous
    level: AutonomyLevel = AutonomyLevel.SUGGEST


class UpdateLogEntry(BaseModel):
    """Single inbound-update record attached to a TaskNode."""

    received_at: datetime
    source: str  # resource_id
    raw_text: str
    parsed_status: str | None = None
    parsed_progress: float | None = None
    matched_by: MatchedBy = MatchedBy.TASK_ID
    match_confidence: float = 1.0
    action_taken: str | None = None


class EmbeddingInputs(BaseModel):
    """Structured inputs used to build the task embedding vector."""

    title: str = ""
    description: str = ""
    goal_context: str = ""
    key_entities: list[str] = []


# ---------------------------------------------------------------------------
# TaskNode
# ---------------------------------------------------------------------------


class TaskNode(BaseNode):
    """Core task node. All 11 task variants use this model with a discriminated
    type_metadata block that carries type-specific fields."""

    # Identity
    task_type: TaskType
    title: str
    description: str

    # Ownership
    created_by: str | None = None  # user_id
    owned_by: str | None = None  # user_id or resource_id
    assigned_to: str | None = None  # resource_id

    # State machine
    state: TaskState = TaskState.PENDING
    state_history: list[StateHistoryEntry] = []

    # Timeline
    timeline: Timeline = Timeline()

    # Priority / scoring
    scoring: ScoringBlock = ScoringBlock()
    on_critical_path: bool = False

    # Progress
    progress: ProgressBlock = ProgressBlock()

    # Human override
    override: OverrideBlock = OverrideBlock()

    # Inbound update log
    update_log: list[UpdateLogEntry] = []

    # Intelligence log
    intelligence: str | None = None
    """Human-readable markdown text blob accumulating the communication log and 
    decisions for this task across all channels. Each entry is formatted as 
    [{ISO-date}] {channel} | {direction} | {summary}."""

    # Type-specific metadata (discriminated union on task_type)
    type_metadata: TypeMetadata | None = None

    # Vector embedding inputs (the float vector is stored in the DB layer)
    embedding_inputs: EmbeddingInputs | None = None

    # Autonomy
    autonomy: AutonomyBlock = AutonomyBlock()

    # Tags
    tags: list[str] = []

    @field_validator("state", mode="before")
    @classmethod
    def _normalise_state(cls, v: object) -> object:
        """Map legacy lowercase state values (written by old tool definitions) to canonical enum values."""
        _legacy = {
            "open": "PENDING",
            "in_progress": "IN_PROGRESS",
            "blocked": "BLOCKED",
            "complete": "COMPLETE",
            "cancelled": "CANCELLED",
            "snoozed": "SNOOZED",
            "active": "ACTIVE",
            "delayed": "DELAYED",
            "needs_review": "NEEDS_REVIEW",
            "inactive_pending": "INACTIVE_PENDING",
        }
        if isinstance(v, str):
            return _legacy.get(v, v)
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not TASK_ID_PATTERN.match(v):
            raise ValueError(f"Invalid task ID '{v}'. Expected TSK-<INITIALS>-<SEQ>-<TYPE_CODE>")
        return v


# ---------------------------------------------------------------------------
# UserNode
# ---------------------------------------------------------------------------


class ScoringWeights(BaseModel):
    """Learned scoring weights for a user; defaults match PRD Section 4.1."""

    W1_timeline: float = 0.25
    W2_dependencies: float = 0.20
    W3_critical_path: float = 0.20
    W4_blocker: float = 0.15
    W5_override: float = 0.10
    W6_resource_risk: float = 0.05
    W7_constraint: float = 0.05
    last_updated: datetime | None = None
    update_count: int = 0


class AutonomyDefaults(BaseModel):
    auto_update_ai_agents: bool = False
    auto_send_followups: bool = False
    auto_close_resolved: bool = False


class UserPreferences(BaseModel):
    briefing_time: str | None = None  # "HH:MM"
    briefing_style: str = "concise"  # "concise" | "detailed"
    default_follow_up_days: int = 3
    interrupt_threshold: float = 0.8
    autonomy_defaults: AutonomyDefaults = AutonomyDefaults()


class BehavioralModel(BaseModel):
    avg_estimate_accuracy: float = 0.0
    preferred_task_batch_size: int = 5
    responsive_hours: list[dict] = []  # list of {start, end}
    decision_speed: str = "variable"  # "fast" | "deliberate" | "variable"
    override_frequency: float = 0.0


class WorkingHours(BaseModel):
    start: str | None = None  # "HH:MM"
    end: str | None = None


class UserNode(BaseNode):
    """Node representing a human user who owns tasks and goals."""

    name: str
    email: str
    role: str | None = None
    timezone: str = "UTC"
    working_hours: WorkingHours = WorkingHours()
    preferences: UserPreferences = UserPreferences()
    scoring_weights: ScoringWeights = ScoringWeights()
    behavioral_model: BehavioralModel = BehavioralModel()

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not USER_ID_PATTERN.match(v):
            raise ValueError(f"Invalid user ID '{v}'. Expected USER-<identifier>")
        return v


# ---------------------------------------------------------------------------
# GoalNode
# ---------------------------------------------------------------------------


class GoalTimeline(BaseModel):
    target_date: datetime | None = None
    completed_at: datetime | None = None


class GoalProgress(BaseModel):
    milestone_count: int = 0
    milestones_done: int = 0
    derived_percentage: float = 0.0


class GoalNode(BaseNode):
    """Node representing a high-level goal owned by a user."""

    title: str
    description: str
    intelligence: str | None = None
    """Human-readable markdown text blob accumulating the communication log and 
    decisions for this goal across all channels. Each entry is formatted as 
    [{ISO-date}] {channel} | {direction} | {summary}."""
    owner: str | None = None  # user_id
    state: GoalState = GoalState.ACTIVE
    timeline: GoalTimeline = GoalTimeline()
    progress: GoalProgress = GoalProgress()
    priority: GoalPriority = GoalPriority.P2
    origin: GoalOrigin = GoalOrigin.USER_DEFINED
    inferred_from: list[str] = []  # task_ids if agent-inferred
    inference_note: str | None = None
    confirmed_by_user: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not GOAL_ID_PATTERN.match(v):
            raise ValueError(f"Invalid goal ID '{v}'. Expected GOAL-<identifier>")
        return v


# ---------------------------------------------------------------------------
# ConstraintNode
# ---------------------------------------------------------------------------


class ConstraintRule(BaseModel):
    """The rule body of a ConstraintNode."""

    hard_limit: bool = False
    threshold: str | None = None  # e.g. "$50,000" or "2024-12-31"
    current_value: str | None = None
    pressure_score: float = 0.0  # 0.0 – 1.0
    breached: bool = False


class ConstraintNode(BaseNode):
    """Node representing a constraint that governs tasks, milestones, or goals."""

    constraint_type: ConstraintType
    title: str
    description: str
    rule: ConstraintRule = ConstraintRule()
    scope: ConstraintScope = ConstraintScope.TASK
    applies_to: list[str] = []  # node_ids
    origin: GoalOrigin = GoalOrigin.USER_DEFINED
    confirmed_by_user: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not CONSTRAINT_ID_PATTERN.match(v):
            raise ValueError(f"Invalid constraint ID '{v}'. Expected CON-<identifier>")
        return v


# ---------------------------------------------------------------------------
# ResourceNode
# ---------------------------------------------------------------------------


class CapacityModel(BaseModel):
    max_concurrent_tasks: int = 5
    current_active_tasks: int = 0
    load_factor: float = 0.0
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    last_signaled_at: datetime | None = None


class ReliabilityModel(BaseModel):
    overall_score: float = 0.8
    on_time_delivery_rate: float = 0.8
    proactive_update_rate: float = 0.5
    response_rate: float = 0.8
    avg_response_time_hrs: float = 24.0
    total_tasks_completed: int = 0
    total_tasks_delayed: int = 0


class RiskSignal(BaseModel):
    signal: str
    inferred_at: datetime
    source_node: str | None = None  # node_id
    expires_at: datetime | None = None


class CurrentRisk(BaseModel):
    capacity_risk: RiskLevel = RiskLevel.LOW
    delivery_risk: RiskLevel = RiskLevel.LOW
    responsiveness_risk: RiskLevel = RiskLevel.LOW
    risk_signals: list[RiskSignal] = []


class CommunicationPreferences(BaseModel):
    preferred_channel: str = "email"  # "email" | "slack" | "api" | "chat"
    batch_messages: bool = False
    batch_window_hours: int = 24


class ResourceNode(BaseNode):
    """Node representing any entity (human or AI agent) that can own or work on tasks."""

    resource_type: ResourceType
    name: str
    contact: str | None = None  # email address or endpoint URL
    timezone: str | None = None
    capacity: CapacityModel = CapacityModel()
    reliability: ReliabilityModel = ReliabilityModel()
    current_risk: CurrentRisk = CurrentRisk()
    communication_preferences: CommunicationPreferences = CommunicationPreferences()

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not RESOURCE_ID_PATTERN.match(v):
            raise ValueError(f"Invalid resource ID '{v}'. Expected RES-<identifier>")
        return v


# ---------------------------------------------------------------------------
# CheckinNode
# ---------------------------------------------------------------------------


class CheckinResolution(BaseModel):
    task_id: str
    action_taken: str
    new_state: str | None = None


class CheckinNode(BaseNode):
    """Batched communication artifact — not a task itself."""

    target_resource: str  # resource_id
    created_by: str = "AGENT"
    task_refs: list[str] = []  # task_ids batched into this check-in
    state: CheckinState = CheckinState.SCHEDULED
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    response_received_at: datetime | None = None
    outbound_message: str | None = None
    inbound_response: str | None = None
    resolution: list[CheckinResolution] = []


# ---------------------------------------------------------------------------
# OrganizationNode  (Phase 2)
# ---------------------------------------------------------------------------


class OrgSettings(BaseModel):
    """Configuration block for an organization."""

    default_workspace_visibility: WorkspaceVisibility = WorkspaceVisibility.INTERNAL
    allow_guest_members: bool = False
    require_approval_for_tasks: bool = False
    daily_briefing_hour_utc: int = 8  # 0-23


class OrgMember(BaseModel):
    """Inline membership record stored on OrganizationNode."""

    user_id: str
    role: OrgRole = OrgRole.MEMBER
    status: MembershipStatus = MembershipStatus.ACTIVE
    joined_at: datetime | None = None


class OrganizationNode(BaseNode):
    """Node representing a multi-user organization workspace boundary.

    Organizations own one or more Workspaces and hold the membership list
    that determines who can see and act on tasks within those workspaces.
    """

    name: str
    domain: str | None = None  # e.g. "acme.com" for SSO matching
    owner_id: str  # USER-{uuid} of the founding user
    members: list[OrgMember] = []
    settings: OrgSettings = OrgSettings()

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not ORG_ID_PATTERN.match(v):
            raise ValueError(f"Invalid org ID '{v}'. Expected ORG-<identifier>")
        return v


# ---------------------------------------------------------------------------
# WorkspaceNode  (Phase 2)
# ---------------------------------------------------------------------------


class WorkspaceNode(BaseNode):
    """Node representing a scoped collection of tasks and goals.

    A workspace belongs to one OrganizationNode and provides an isolation
    boundary for tasks, goals, and briefings.  All tasks/goals that are
    SCOPED_TO_WS this node are only visible to workspace members.
    """

    org_id: str  # ORG-{uuid} of the parent org
    name: str
    description: str = ""
    visibility: WorkspaceVisibility = WorkspaceVisibility.INTERNAL
    task_prefix: str = ""  # User-initials prefix for task IDs in this workspace
    member_ids: list[str] = []  # USER-{uuid} list (subset of org members)
    is_default: bool = False  # True for the org's default workspace

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not WORKSPACE_ID_PATTERN.match(v):
            raise ValueError(f"Invalid workspace ID '{v}'. Expected WS-<identifier>")
        return v


# ---------------------------------------------------------------------------
# VisibilityGrantNode  (Phase 3 — Multi-User Visibility Grants)
# ---------------------------------------------------------------------------


class VisibilityGrantNode(BaseNode):
    """Grants a specific user access to a specific node at a given scope.

    Forward edge: GRANTS_ACCESS_TO from VisibilityGrantNode -> target node.
    Reverse lookup: query by (granted_to_user_id, target_node_id) to check access.
    Grants are permanent until explicitly revoked (deleted from graph).
    """

    grantor_user_id: str = Field(..., description="USER-{id} of user granting access.")
    granted_to_user_id: str = Field(..., description="USER-{id} receiving the grant.")
    target_node_id: str = Field(..., description="ID of the node being shared.")
    target_node_type: str = Field(..., description="Node label (e.g. 'TaskNode', 'GoalNode').")
    scope: VisibilityScope = VisibilityScope.VIEWER
    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    reason: str = ""

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        from graphclaw.models.base import validate_grant_id

        return validate_grant_id(v)


# ---------------------------------------------------------------------------
# MCPServerNode  (Phase 4 — MCP Server Integration)
# ---------------------------------------------------------------------------


class MCPServerNode(BaseNode):
    """Registered MCP server for a user. Stores transport config and trust settings.

    Each user maintains a personal MCP Registry — a list of registered MCP
    servers persisted to the graph DB as MCPServerNode vertices.  The trust
    tier governs whether tool calls are executed automatically (AUTO), require
    user approval (GATED), or are rejected entirely (BLOCKED).
    """

    node_type: str = Field(default="MCPServerNode", frozen=True)
    name: str = Field(..., description="Human-readable name, e.g. 'Google Calendar'")
    transport: MCPTransport = Field(default=MCPTransport.HTTP)
    endpoint_url: str | None = Field(default=None, description="URL for sse/http transports")
    command: str | None = Field(default=None, description="Command for stdio transport")
    trust_tier: TrustTier = Field(default=TrustTier.GATED)
    scope: list[str] = Field(default_factory=list, description="Declared capability scopes")
    secret_ref: str | None = Field(default=None, description="Secrets Manager key ID for auth")
    enabled: bool = Field(default=True)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = Field(default=None)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not MCP_SERVER_ID_PATTERN.match(v):
            raise ValueError(f"Invalid MCP server ID '{v}'. Expected MCP-<identifier>")
        return v


__all__ = [
    # Sub-models
    "Timeline",
    "ScoringBlock",
    "StateHistoryEntry",
    "ProgressBlock",
    "OverrideBlock",
    "AutonomyBlock",
    "UpdateLogEntry",
    "EmbeddingInputs",
    "ScoringWeights",
    "AutonomyDefaults",
    "UserPreferences",
    "BehavioralModel",
    "WorkingHours",
    "GoalTimeline",
    "GoalProgress",
    "ConstraintRule",
    "CapacityModel",
    "ReliabilityModel",
    "RiskSignal",
    "CurrentRisk",
    "CommunicationPreferences",
    "CheckinResolution",
    # Node types
    "TaskNode",
    "UserNode",
    "GoalNode",
    "ConstraintNode",
    "ResourceNode",
    "CheckinNode",
    # Phase 2
    "OrgSettings",
    "OrgMember",
    "OrganizationNode",
    "WorkspaceNode",
    # Phase 3
    "VisibilityGrantNode",
    "VisibilityScope",
    # Phase 4
    "MCPServerNode",
]
