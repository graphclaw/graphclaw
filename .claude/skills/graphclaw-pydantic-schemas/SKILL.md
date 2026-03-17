---
name: graphclaw-pydantic-schemas
description: Pydantic model conventions for GraphClaw node types, edge types, and validation rules. Use when creating, modifying, or reviewing Pydantic models for graph nodes, edges, scoring records, or API types.
---

# GraphClaw Pydantic Schema Conventions

## ID Formats

```python
import re
from pydantic import field_validator

TASK_ID_PATTERN = re.compile(r'^TSK-[A-Z]{2,}-\d{4,}-(?:DEL|ATM|FLW|CMP|APR|MIL|RVW|REC|DEC|CHK|RES)$')
USER_ID_PATTERN = re.compile(r'^USER-[\w-]+$')
GOAL_ID_PATTERN = re.compile(r'^GOAL-[\w-]+$')
CONSTRAINT_ID_PATTERN = re.compile(r'^CON-[\w-]+$')
RESOURCE_ID_PATTERN = re.compile(r'^RES-[\w-]+$')
EDGE_ID_PATTERN = re.compile(r'^EDGE-[\w-]+$')
```

Type codes: DEL=Delegated, ATM=Atomic, FLW=Follow-up, CMP=Composite, APR=Approval, MIL=Milestone, RVW=Review, REC=Recurring, DEC=Decision, CHK=Check-in, RES=Research

## Enums

```python
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

class GateType(str, Enum):
    AND = "AND"
    OR = "OR"

class GoalPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
```

## Base Node Model

```python
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class BaseNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
```

## TaskNode — Discriminated Union on task_type

```python
class Timeline(BaseModel):
    deadline: Optional[datetime] = None
    estimated_effort_hours: Optional[float] = None
    completed_at: Optional[datetime] = None

class ScoringBlock(BaseModel):
    timeline_urgency: float = 0.0      # 0.0-1.2
    dependency_weight: float = 0.0
    critical_path: float = 0.0         # 0.0 or 1.0
    blocker: float = 0.0               # 0.0, 0.6, or 1.0
    human_override: float = 0.0        # -0.3 to +1.0
    resource_risk: float = 0.0         # 0.0-1.0
    constraint_pressure: float = 0.0   # 0.0-1.0

class StateHistoryEntry(BaseModel):
    from_state: TaskState
    to_state: TaskState
    changed_at: datetime
    changed_by: str  # "AGENT", "HUMAN", "SYSTEM", "CASCADE"
    reason: Optional[str] = None

class TaskNode(BaseNode):
    task_type: TaskType
    title: str
    description: str
    state: TaskState = TaskState.PENDING
    state_history: list[StateHistoryEntry] = []
    timeline: Timeline = Timeline()
    scoring: ScoringBlock = ScoringBlock()
    progress: Optional[float] = None   # 0.0-1.0
    override: Optional[str] = None
    update_log: list[dict] = []
    embedding_inputs: Optional[str] = None
    autonomy: str = "SUGGEST"          # SUGGEST | AUTONOMOUS | REQUIRE_APPROVAL
    on_critical_path: bool = False
```

## type_metadata — Per-Task-Type

```python
from typing import Annotated, Union, Literal
from pydantic import Field

class DelegatedMetadata(BaseModel):
    task_type: Literal[TaskType.DELEGATED] = TaskType.DELEGATED
    assigned_resource_id: str
    follow_up_task_id: Optional[str] = None

class FollowUpMetadata(BaseModel):
    task_type: Literal[TaskType.FOLLOWUP] = TaskType.FOLLOWUP
    target_task_id: str
    scheduled_fire_at: datetime
    follow_up_count: int = 0

class ApprovalMetadata(BaseModel):
    task_type: Literal[TaskType.APPROVAL] = TaskType.APPROVAL
    approver_id: str
    max_wait_days: Optional[int] = None
    escalation_target: Optional[str] = None

class CompositeMetadata(BaseModel):
    task_type: Literal[TaskType.COMPOSITE] = TaskType.COMPOSITE
    child_task_ids: list[str] = []
    completion_gate: GateType = GateType.AND

TypeMetadata = Annotated[
    Union[DelegatedMetadata, FollowUpMetadata, ApprovalMetadata, CompositeMetadata, ...],
    Field(discriminator='task_type')
]
```

## Other Node Types

```python
class UserNode(BaseNode):
    name: str
    email: str
    timezone: str = "UTC"
    scoring_weights: dict[str, float] = {
        "W1": 0.25, "W2": 0.20, "W3": 0.20, "W4": 0.15,
        "W5": 0.10, "W6": 0.05, "W7": 0.05
    }
    preferences: dict = {}

class GoalNode(BaseNode):
    title: str
    description: str
    state: TaskState = TaskState.ACTIVE
    priority: GoalPriority = GoalPriority.P2
    progress: float = 0.0

class ConstraintNode(BaseNode):
    constraint_type: str  # DEADLINE, BUDGET, COMPLIANCE, EXTERNAL
    rule: dict            # {threshold, pressure_score, ...}
    scope: str
    applies_to: list[str] = []

class ResourceNode(BaseNode):
    resource_type: str    # HUMAN, AI_AGENT
    name: str
    capacity: float = 1.0
    reliability_score: float = 0.8
    current_load_factor: float = 0.0
```

## ScoreExplanation

```python
class ScoreFactor(BaseModel):
    factor_name: str
    raw_score: float
    weight: float
    weighted_score: float
    plain_english: str

class ScoreExplanation(BaseModel):
    node_id: str
    scored_at: datetime
    final_score: float
    rank: int
    factors: list[ScoreFactor]
    modifiers: list[dict] = []
    summary: str
    topology_note: Optional[str] = None
```

## Conventions

- All timestamps as `datetime` (UTC)
- All scores/weights as `float`, validated 0.0-1.0 where appropriate
- All IDs as `str` with regex validators
- Use `model_config = ConfigDict(from_attributes=True)` for ORM compatibility
- Discriminated unions via `Annotated[Union[...], Field(discriminator='field')]`
