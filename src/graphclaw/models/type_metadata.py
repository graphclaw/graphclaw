"""graphclaw.models.type_metadata — Per-task-type metadata with discriminated union.

Description
-----------
Defines 11 task-type-specific metadata models (one per ``TaskType`` variant) and
the ``TypeMetadata`` annotated union that ``TaskNode.type_metadata`` uses as its
field type.  Pydantic v2's discriminated union on ``task_type`` ensures that the
correct sub-model is deserialised and validated based on the literal tag in the
incoming data.

Design Patterns
---------------
- Discriminated Union: ``TypeMetadata`` uses ``Annotated[Union[...], Field(discriminator="task_type")]``
  so that Pydantic can select the correct sub-model at parse time without
  inspecting all 11 shapes.
- Literal Type Tags: Each sub-model carries ``task_type: Literal[TaskType.X]``
  as the discriminator field, making the union self-describing.

Public API
----------
- AtomicMetadata: Simple self-contained task — no sub-tasks.
- DelegatedMetadata: Task delegated to a resource; tracks outbound message state.
- FollowUpMetadata: Monitors a delegated task; has a scheduled fire time.
- ApprovalMetadata: Requires explicit human approval; tracks approver and criteria.
- CompositeMetadata: Parent task with child tasks; configures gate and strategy.
- MilestoneMetadata: Marks a significant achievement; notifies resources on completion.
- ReviewMetadata: Reviews another task or deliverable; carries confidence level.
- RecurringMetadata: Spawns instances on a cron-like schedule.
- DecisionMetadata: Branches the workflow based on a choice; tracks activated branches.
- CheckinMetadata: Scheduled interaction artifact targeting a resource.
- ResearchMetadata: Information-gathering task; tracks outputs and confidence.
- TypeMetadata: The discriminated union alias used as the field type in TaskNode.

Dependencies
------------
- graphclaw.models.enums: BreakdownStrategy, ConfidenceLevel, GateType, TaskType.
- pydantic: BaseModel, Field, Annotated.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from graphclaw.models.enums import (
    BreakdownStrategy,
    ConfidenceLevel,
    GateType,
    TaskType,
)


class AtomicMetadata(BaseModel):
    """Metadata for a simple, self-contained task with no sub-tasks."""

    task_type: Literal[TaskType.ATOMIC] = TaskType.ATOMIC


class DelegatedMetadata(BaseModel):
    """Metadata for a task delegated to a resource (human or AI agent)."""

    task_type: Literal[TaskType.DELEGATED] = TaskType.DELEGATED
    assigned_resource_id: str
    expected_deliverable: str | None = None
    outbound_message_sent: str | None = None
    task_id_in_message: bool = False
    follow_up_task_id: str | None = None


class FollowUpMetadata(BaseModel):
    """Metadata for a follow-up task that monitors a delegated task."""

    task_type: Literal[TaskType.FOLLOWUP] = TaskType.FOLLOWUP
    target_task_id: str
    parent_delegated_id: str | None = None
    scheduled_fire_at: datetime
    fire_reason: str | None = None
    follow_up_count: int = 0
    resolved_by_proactive: bool = False
    resolution_source: str | None = None  # update_log_id


class ApprovalMetadata(BaseModel):
    """Metadata for a task requiring explicit human approval."""

    task_type: Literal[TaskType.APPROVAL] = TaskType.APPROVAL
    approver_id: str
    approval_criteria: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None

    # Phase 3 — Cross-User Delegation + Approval Escalation fields
    max_wait_days: int = Field(
        default=7, ge=1, le=90, description="Days before escalation triggers"
    )
    escalation_target_user_id: str | None = Field(
        default=None, description="User to escalate to after max_wait_days"
    )
    escalation_action: str = Field(
        default="REASSIGN", description="REASSIGN | CANCEL | AUTO_APPROVE"
    )
    delegated_by_user_id: str | None = Field(default=None, description="Original delegating user")
    artifact_required: bool = Field(
        default=False, description="Whether artifact submission is required"
    )
    artifact_submitted_at: datetime | None = None
    artifact_storage_key: str | None = None


class CompositeMetadata(BaseModel):
    """Metadata for a parent task composed of child sub-tasks."""

    task_type: Literal[TaskType.COMPOSITE] = TaskType.COMPOSITE
    child_task_ids: list[str] = []
    completion_gate: GateType = GateType.AND
    breakdown_strategy: BreakdownStrategy = BreakdownStrategy.PARALLEL
    auto_complete_on_children: bool = True


class MilestoneMetadata(BaseModel):
    """Metadata for a milestone that marks a significant achievement."""

    task_type: Literal[TaskType.MILESTONE] = TaskType.MILESTONE
    milestone_criteria: str | None = None
    notifies: list[str] = []  # resource_ids to notify on completion
    child_task_count: int = 0
    child_tasks_complete: int = 0


class ReviewMetadata(BaseModel):
    """Metadata for a review task targeting another task or deliverable."""

    task_type: Literal[TaskType.REVIEW] = TaskType.REVIEW
    review_target_id: str
    review_criteria: str | None = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM


class RecurringMetadata(BaseModel):
    """Metadata for a recurring task that spawns instances on a schedule."""

    task_type: Literal[TaskType.RECURRING] = TaskType.RECURRING
    cron_expression: str  # recurrence rule in cron-like format
    last_spawned_at: datetime | None = None
    next_spawn_at: datetime | None = None
    spawn_history: list[str] = []  # list of spawned task_ids


class DecisionMetadata(BaseModel):
    """Metadata for a decision task that branches workflow based on a choice."""

    task_type: Literal[TaskType.DECISION] = TaskType.DECISION
    options: list[str] = []
    decision_made: str | None = None
    decision_deadline: datetime | None = None
    branches_activated: list[str] = []  # task_ids of activated branches
    branches_pruned: list[str] = []  # task_ids of pruned branches


class CheckinMetadata(BaseModel):
    """Metadata for a check-in task (scheduled interaction artifact)."""

    task_type: Literal[TaskType.CHECKIN] = TaskType.CHECKIN
    target_resource_id: str | None = None
    scheduled_for: datetime | None = None


class ResearchMetadata(BaseModel):
    """Metadata for a research task that gathers information to inform decisions."""

    task_type: Literal[TaskType.RESEARCH] = TaskType.RESEARCH
    research_scope: str | None = None  # completion_threshold / definition of done
    outputs: list[str] = []
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_to_proceed: float = 0.0


# ---------------------------------------------------------------------------
# Discriminated union — used as TaskNode.type_metadata field type
# ---------------------------------------------------------------------------

TypeMetadata = Annotated[
    AtomicMetadata
    | DelegatedMetadata
    | FollowUpMetadata
    | ApprovalMetadata
    | CompositeMetadata
    | MilestoneMetadata
    | ReviewMetadata
    | RecurringMetadata
    | DecisionMetadata
    | CheckinMetadata
    | ResearchMetadata,
    Field(discriminator="task_type"),
]

__all__ = [
    "AtomicMetadata",
    "DelegatedMetadata",
    "FollowUpMetadata",
    "ApprovalMetadata",
    "CompositeMetadata",
    "MilestoneMetadata",
    "ReviewMetadata",
    "RecurringMetadata",
    "DecisionMetadata",
    "CheckinMetadata",
    "ResearchMetadata",
    "TypeMetadata",
]
