"""Per-task-type metadata models with discriminated union for GraphClaw TaskNode."""

from datetime import datetime
from typing import Annotated, Literal, Optional, Union

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
    expected_deliverable: Optional[str] = None
    outbound_message_sent: Optional[str] = None
    task_id_in_message: bool = False
    follow_up_task_id: Optional[str] = None


class FollowUpMetadata(BaseModel):
    """Metadata for a follow-up task that monitors a delegated task."""

    task_type: Literal[TaskType.FOLLOWUP] = TaskType.FOLLOWUP
    target_task_id: str
    parent_delegated_id: Optional[str] = None
    scheduled_fire_at: datetime
    fire_reason: Optional[str] = None
    follow_up_count: int = 0
    resolved_by_proactive: bool = False
    resolution_source: Optional[str] = None  # update_log_id


class ApprovalMetadata(BaseModel):
    """Metadata for a task requiring explicit human approval."""

    task_type: Literal[TaskType.APPROVAL] = TaskType.APPROVAL
    approver_id: str
    approval_criteria: Optional[str] = None
    max_wait_days: Optional[int] = None
    escalation_target: Optional[str] = None
    escalation_action: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


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
    milestone_criteria: Optional[str] = None
    notifies: list[str] = []  # resource_ids to notify on completion
    child_task_count: int = 0
    child_tasks_complete: int = 0


class ReviewMetadata(BaseModel):
    """Metadata for a review task targeting another task or deliverable."""

    task_type: Literal[TaskType.REVIEW] = TaskType.REVIEW
    review_target_id: str
    review_criteria: Optional[str] = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM


class RecurringMetadata(BaseModel):
    """Metadata for a recurring task that spawns instances on a schedule."""

    task_type: Literal[TaskType.RECURRING] = TaskType.RECURRING
    cron_expression: str  # recurrence rule in cron-like format
    last_spawned_at: Optional[datetime] = None
    next_spawn_at: Optional[datetime] = None
    spawn_history: list[str] = []  # list of spawned task_ids


class DecisionMetadata(BaseModel):
    """Metadata for a decision task that branches workflow based on a choice."""

    task_type: Literal[TaskType.DECISION] = TaskType.DECISION
    options: list[str] = []
    decision_made: Optional[str] = None
    decision_deadline: Optional[datetime] = None
    branches_activated: list[str] = []  # task_ids of activated branches
    branches_pruned: list[str] = []  # task_ids of pruned branches


class CheckinMetadata(BaseModel):
    """Metadata for a check-in task (scheduled interaction artifact)."""

    task_type: Literal[TaskType.CHECKIN] = TaskType.CHECKIN
    target_resource_id: Optional[str] = None
    scheduled_for: Optional[datetime] = None


class ResearchMetadata(BaseModel):
    """Metadata for a research task that gathers information to inform decisions."""

    task_type: Literal[TaskType.RESEARCH] = TaskType.RESEARCH
    research_scope: Optional[str] = None  # completion_threshold / definition of done
    outputs: list[str] = []
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_to_proceed: float = 0.0


# ---------------------------------------------------------------------------
# Discriminated union — used as TaskNode.type_metadata field type
# ---------------------------------------------------------------------------

TypeMetadata = Annotated[
    Union[
        AtomicMetadata,
        DelegatedMetadata,
        FollowUpMetadata,
        ApprovalMetadata,
        CompositeMetadata,
        MilestoneMetadata,
        ReviewMetadata,
        RecurringMetadata,
        DecisionMetadata,
        CheckinMetadata,
        ResearchMetadata,
    ],
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
