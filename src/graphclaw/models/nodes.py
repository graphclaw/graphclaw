"""GraphClaw graph node Pydantic models.

All node types share BaseNode (id, created_at, updated_at).  TaskNode uses
a discriminated union on task_type for its type_metadata block.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from graphclaw.models.base import (
    CONSTRAINT_ID_PATTERN,
    GOAL_ID_PATTERN,
    RESOURCE_ID_PATTERN,
    TASK_ID_PATTERN,
    USER_ID_PATTERN,
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
    OverrideType,
    ResourceType,
    RiskLevel,
    TaskState,
    TaskType,
)
from graphclaw.models.type_metadata import TypeMetadata


# ---------------------------------------------------------------------------
# Sub-models shared by TaskNode
# ---------------------------------------------------------------------------


class Timeline(BaseModel):
    """Timeline block within a TaskNode."""

    deadline: Optional[datetime] = None
    started_at: Optional[datetime] = None
    estimated_effort_hours: Optional[float] = None
    estimated_effort_days: Optional[float] = None
    actual_effort_days: Optional[float] = None
    completed_at: Optional[datetime] = None


class ScoringBlock(BaseModel):
    """Stores the 7 raw scoring factor values for a TaskNode."""

    timeline_urgency: float = 0.0        # W1: 0.0 – 1.2
    dependency_weight: float = 0.0       # W2
    critical_path: float = 0.0           # W3: 0.0 or 1.0
    blocker: float = 0.0                 # W4: 0.0, 0.6, or 1.0
    human_override: float = 0.0          # W5: -0.3 to +1.0
    resource_risk: float = 0.0           # W6: 0.0 – 1.0
    constraint_pressure: float = 0.0     # W7: 0.0 – 1.0
    computed_priority: float = 0.0       # final weighted score
    chain_urgency_rollup: float = 0.0
    last_scored_at: Optional[datetime] = None
    score_reasoning: Optional[str] = None


class StateHistoryEntry(BaseModel):
    """Single state-transition record, appended on every transition."""

    from_state: TaskState
    to_state: TaskState
    changed_at: datetime
    changed_by: ChangedBy
    reason: Optional[str] = None


class ProgressBlock(BaseModel):
    """Task progress tracking sub-model."""

    percentage: float = 0.0
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    last_update: Optional[datetime] = None
    completion_signal: CompletionSignal = CompletionSignal.EXPLICIT


class OverrideBlock(BaseModel):
    """Human-override sub-model for a TaskNode."""

    is_overridden: bool = False
    override_type: Optional[OverrideType] = None
    override_note: Optional[str] = None
    set_by: Optional[str] = None         # user_id
    set_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class AutonomyBlock(BaseModel):
    """Per-node autonomy permission overrides."""

    auto_update_allowed: bool = False
    auto_close_allowed: bool = False
    requires_approval_from: Optional[str] = None  # user_id; null = fully autonomous
    level: AutonomyLevel = AutonomyLevel.SUGGEST


class UpdateLogEntry(BaseModel):
    """Single inbound-update record attached to a TaskNode."""

    received_at: datetime
    source: str                           # resource_id
    raw_text: str
    parsed_status: Optional[str] = None
    parsed_progress: Optional[float] = None
    matched_by: MatchedBy = MatchedBy.TASK_ID
    match_confidence: float = 1.0
    action_taken: Optional[str] = None


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
    created_by: Optional[str] = None     # user_id
    owned_by: Optional[str] = None       # user_id or resource_id
    assigned_to: Optional[str] = None    # resource_id

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

    # Type-specific metadata (discriminated union on task_type)
    type_metadata: Optional[TypeMetadata] = None

    # Vector embedding inputs (the float vector is stored in the DB layer)
    embedding_inputs: Optional[EmbeddingInputs] = None

    # Autonomy
    autonomy: AutonomyBlock = AutonomyBlock()

    # Tags
    tags: list[str] = []

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not TASK_ID_PATTERN.match(v):
            raise ValueError(
                f"Invalid task ID '{v}'. Expected TSK-<INITIALS>-<SEQ>-<TYPE_CODE>"
            )
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
    last_updated: Optional[datetime] = None
    update_count: int = 0


class AutonomyDefaults(BaseModel):
    auto_update_ai_agents: bool = False
    auto_send_followups: bool = False
    auto_close_resolved: bool = False


class UserPreferences(BaseModel):
    briefing_time: Optional[str] = None  # "HH:MM"
    briefing_style: str = "concise"      # "concise" | "detailed"
    default_follow_up_days: int = 3
    interrupt_threshold: float = 0.8
    autonomy_defaults: AutonomyDefaults = AutonomyDefaults()


class BehavioralModel(BaseModel):
    avg_estimate_accuracy: float = 0.0
    preferred_task_batch_size: int = 5
    responsive_hours: list[dict] = []    # list of {start, end}
    decision_speed: str = "variable"     # "fast" | "deliberate" | "variable"
    override_frequency: float = 0.0


class WorkingHours(BaseModel):
    start: Optional[str] = None          # "HH:MM"
    end: Optional[str] = None


class UserNode(BaseNode):
    """Node representing a human user who owns tasks and goals."""

    name: str
    email: str
    role: Optional[str] = None
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
    target_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class GoalProgress(BaseModel):
    milestone_count: int = 0
    milestones_done: int = 0
    derived_percentage: float = 0.0


class GoalNode(BaseNode):
    """Node representing a high-level goal owned by a user."""

    title: str
    description: str
    owner: Optional[str] = None          # user_id
    state: GoalState = GoalState.ACTIVE
    timeline: GoalTimeline = GoalTimeline()
    progress: GoalProgress = GoalProgress()
    priority: GoalPriority = GoalPriority.P2
    origin: GoalOrigin = GoalOrigin.USER_DEFINED
    inferred_from: list[str] = []        # task_ids if agent-inferred
    inference_note: Optional[str] = None
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
    threshold: Optional[str] = None      # e.g. "$50,000" or "2024-12-31"
    current_value: Optional[str] = None
    pressure_score: float = 0.0          # 0.0 – 1.0
    breached: bool = False


class ConstraintNode(BaseNode):
    """Node representing a constraint that governs tasks, milestones, or goals."""

    constraint_type: ConstraintType
    title: str
    description: str
    rule: ConstraintRule = ConstraintRule()
    scope: ConstraintScope = ConstraintScope.TASK
    applies_to: list[str] = []           # node_ids
    origin: GoalOrigin = GoalOrigin.USER_DEFINED
    confirmed_by_user: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not CONSTRAINT_ID_PATTERN.match(v):
            raise ValueError(
                f"Invalid constraint ID '{v}'. Expected CON-<identifier>"
            )
        return v


# ---------------------------------------------------------------------------
# ResourceNode
# ---------------------------------------------------------------------------


class CapacityModel(BaseModel):
    max_concurrent_tasks: int = 5
    current_active_tasks: int = 0
    load_factor: float = 0.0
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    last_signaled_at: Optional[datetime] = None


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
    source_node: Optional[str] = None   # node_id
    expires_at: Optional[datetime] = None


class CurrentRisk(BaseModel):
    capacity_risk: RiskLevel = RiskLevel.LOW
    delivery_risk: RiskLevel = RiskLevel.LOW
    responsiveness_risk: RiskLevel = RiskLevel.LOW
    risk_signals: list[RiskSignal] = []


class CommunicationPreferences(BaseModel):
    preferred_channel: str = "email"    # "email" | "slack" | "api" | "chat"
    batch_messages: bool = False
    batch_window_hours: int = 24


class ResourceNode(BaseNode):
    """Node representing any entity (human or AI agent) that can own or work on tasks."""

    resource_type: ResourceType
    name: str
    contact: Optional[str] = None       # email address or endpoint URL
    timezone: Optional[str] = None
    capacity: CapacityModel = CapacityModel()
    reliability: ReliabilityModel = ReliabilityModel()
    current_risk: CurrentRisk = CurrentRisk()
    communication_preferences: CommunicationPreferences = CommunicationPreferences()

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not RESOURCE_ID_PATTERN.match(v):
            raise ValueError(
                f"Invalid resource ID '{v}'. Expected RES-<identifier>"
            )
        return v


# ---------------------------------------------------------------------------
# CheckinNode
# ---------------------------------------------------------------------------


class CheckinResolution(BaseModel):
    task_id: str
    action_taken: str
    new_state: Optional[str] = None


class CheckinNode(BaseNode):
    """Batched communication artifact — not a task itself."""

    target_resource: str                  # resource_id
    created_by: str = "AGENT"
    task_refs: list[str] = []             # task_ids batched into this check-in
    state: CheckinState = CheckinState.SCHEDULED
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    response_received_at: Optional[datetime] = None
    outbound_message: Optional[str] = None
    inbound_response: Optional[str] = None
    resolution: list[CheckinResolution] = []


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
]
