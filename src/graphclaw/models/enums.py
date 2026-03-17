"""GraphClaw domain enumerations."""

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
