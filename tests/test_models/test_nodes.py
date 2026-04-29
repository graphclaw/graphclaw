"""Tests for GraphClaw domain models (WS-B).

Covers:
- Creating each node type with valid data
- ID format validation (valid and invalid)
- TaskState and TaskType enum completeness
- TypeMetadata discriminated union
- ScoreExplanation construction
- GraphEdge construction
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from graphclaw.models.base import (
    CONSTRAINT_ID_PATTERN,
    EDGE_ID_PATTERN,
    GOAL_ID_PATTERN,
    HANDOFF_NODE_ID_PATTERN,
    RESOURCE_ID_PATTERN,
    TASK_ID_PATTERN,
    USER_ID_PATTERN,
    generate_constraint_id,
    generate_edge_id,
    generate_goal_id,
    generate_handoff_node_id,
    generate_resource_id,
    generate_task_id,
    generate_user_id,
)
from graphclaw.models.edges import (
    EdgeProperties,
    GraphEdge,
)
from graphclaw.models.enums import (
    AutonomyLevel,
    ChangedBy,
    ConstraintType,
    EdgeType,
    GateType,
    GoalPriority,
    GoalState,
    ResourceType,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import (
    CheckinNode,
    ConstraintNode,
    ConstraintRule,
    GoalNode,
    HandoffNode,
    ResourceNode,
    ScoringBlock,
    StateHistoryEntry,
    TaskNode,
    Timeline,
    UserNode,
)
from graphclaw.models.scoring import (
    ActionQueueEntry,
    ScoreExplanation,
    ScoreFactor,
    ScoreModifier,
)
from graphclaw.models.type_metadata import (
    ApprovalMetadata,
    AtomicMetadata,
    CheckinMetadata,
    CompositeMetadata,
    DecisionMetadata,
    DelegatedMetadata,
    FollowUpMetadata,
    MilestoneMetadata,
    RecurringMetadata,
    ResearchMetadata,
    ReviewMetadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)


def _task_id(task_type: TaskType, initials: str = "AB") -> str:
    """Generate a valid task ID for a given type."""
    return generate_task_id(initials, task_type)


# ---------------------------------------------------------------------------
# ID format tests
# ---------------------------------------------------------------------------


class TestIDPatterns:
    def test_task_id_valid_formats(self):
        valid = [
            "TSK-AB-1234-ATM",
            "TSK-ABC-0001-DEL",
            "TSK-XY-9999-RES",
            "TSK-AB-0000-CHK",
        ]
        for v in valid:
            assert TASK_ID_PATTERN.match(v), f"Expected valid: {v}"

    def test_task_id_invalid_formats(self):
        invalid = [
            "TSK-A-1234-ATM",  # initials too short (1 char)
            "TSK-ab-1234-ATM",  # lowercase initials
            "TSK-AB-123-ATM",  # sequence too short (3 digits)
            "TSK-AB-1234-XXX",  # unknown type code
            "tsk-AB-1234-ATM",  # lowercase prefix
            "TASK-AB-1234-ATM",  # wrong prefix
            "",
        ]
        for v in invalid:
            assert not TASK_ID_PATTERN.match(v), f"Expected invalid: {v}"

    def test_user_id_valid(self):
        assert USER_ID_PATTERN.match("USER-abc-123")
        assert USER_ID_PATTERN.match(generate_user_id())

    def test_user_id_invalid(self):
        assert not USER_ID_PATTERN.match("USR-abc")
        assert not USER_ID_PATTERN.match("user-abc")

    def test_goal_id_valid(self):
        assert GOAL_ID_PATTERN.match("GOAL-abc-123")
        assert GOAL_ID_PATTERN.match(generate_goal_id())

    def test_constraint_id_valid(self):
        assert CONSTRAINT_ID_PATTERN.match("CON-abc-123")
        assert CONSTRAINT_ID_PATTERN.match(generate_constraint_id())

    def test_resource_id_valid(self):
        assert RESOURCE_ID_PATTERN.match("RES-abc-123")
        assert RESOURCE_ID_PATTERN.match(generate_resource_id())

    def test_edge_id_valid(self):
        assert EDGE_ID_PATTERN.match("EDGE-abc-123")
        assert EDGE_ID_PATTERN.match(generate_edge_id())

    def test_handoff_id_valid(self):
        assert HANDOFF_NODE_ID_PATTERN.match("HND-abc-123")
        assert HANDOFF_NODE_ID_PATTERN.match(generate_handoff_node_id())

    def test_generated_task_ids_match_all_types(self):
        for task_type in TaskType:
            if task_type == TaskType.CHECKIN:
                # CHECKIN uses CHK code
                tid = generate_task_id("AB", task_type)
                assert TASK_ID_PATTERN.match(tid), f"Generated ID invalid for {task_type}: {tid}"
            else:
                tid = generate_task_id("AB", task_type)
                assert TASK_ID_PATTERN.match(tid), f"Generated ID invalid for {task_type}: {tid}"


# ---------------------------------------------------------------------------
# Enum completeness tests
# ---------------------------------------------------------------------------


class TestEnumCompleteness:
    def test_task_state_has_10_values(self):
        states = set(TaskState)
        assert len(states) == 10, f"Expected 10 TaskState values, got {len(states)}"

    def test_task_state_expected_values(self):
        expected = {
            "PENDING",
            "ACTIVE",
            "IN_PROGRESS",
            "BLOCKED",
            "DELAYED",
            "NEEDS_REVIEW",
            "COMPLETE",
            "CANCELLED",
            "SNOOZED",
            "INACTIVE_PENDING",
        }
        actual = {s.value for s in TaskState}
        assert actual == expected

    def test_task_type_has_11_values(self):
        types = set(TaskType)
        assert len(types) == 11, f"Expected 11 TaskType values, got {len(types)}"

    def test_task_type_expected_values(self):
        expected = {
            "ATOMIC",
            "COMPOSITE",
            "DELEGATED",
            "FOLLOWUP",
            "APPROVAL",
            "MILESTONE",
            "REVIEW",
            "RECURRING",
            "DECISION",
            "CHECKIN",
            "RESEARCH",
        }
        actual = {t.value for t in TaskType}
        assert actual == expected

    def test_edge_type_has_at_least_8_values(self):
        # PRD lists 8 primary + 3 extended = 11 in requirements; skill doc shows 8
        assert len(set(EdgeType)) >= 8

    def test_gate_type_has_and_or(self):
        assert GateType.AND.value == "AND"
        assert GateType.OR.value == "OR"

    def test_goal_priority_values(self):
        assert {p.value for p in GoalPriority} == {"P1", "P2", "P3"}


# ---------------------------------------------------------------------------
# TaskNode construction tests
# ---------------------------------------------------------------------------


class TestTaskNode:
    def _make(self, task_type: TaskType, **kwargs):
        return TaskNode(
            id=_task_id(task_type),
            task_type=task_type,
            title="Test task",
            description="A test task description",
            created_at=NOW,
            updated_at=NOW,
            **kwargs,
        )

    def test_atomic_task_defaults(self):
        node = self._make(TaskType.ATOMIC)
        assert node.state == TaskState.PENDING
        assert node.on_critical_path is False
        assert node.tags == []
        assert node.autonomy.level == AutonomyLevel.SUGGEST

    def test_task_with_state_history(self):
        entry = StateHistoryEntry(
            from_state=TaskState.PENDING,
            to_state=TaskState.ACTIVE,
            changed_at=NOW,
            changed_by=ChangedBy.AGENT,
            reason="Activated by agent",
        )
        node = self._make(TaskType.ATOMIC, state=TaskState.ACTIVE, state_history=[entry])
        assert len(node.state_history) == 1
        assert node.state_history[0].changed_by == ChangedBy.AGENT

    def test_task_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            TaskNode(
                id="BAD-ID",
                task_type=TaskType.ATOMIC,
                title="x",
                description="x",
                created_at=NOW,
                updated_at=NOW,
            )

    def test_task_with_scoring_block(self):
        scoring = ScoringBlock(
            timeline_urgency=0.8,
            critical_path=1.0,
            computed_priority=0.75,
        )
        node = self._make(TaskType.ATOMIC, scoring=scoring, on_critical_path=True)
        assert node.scoring.timeline_urgency == 0.8
        assert node.on_critical_path is True

    def test_task_with_timeline(self):
        tl = Timeline(deadline=NOW, estimated_effort_hours=8.0)
        node = self._make(TaskType.ATOMIC, timeline=tl)
        assert node.timeline.deadline == NOW

    def test_task_with_tags(self):
        node = self._make(TaskType.ATOMIC, tags=["urgent", "backend"])
        assert "urgent" in node.tags

    def test_all_task_types_can_be_constructed(self):
        for tt in TaskType:
            node = self._make(tt)
            assert node.task_type == tt


# ---------------------------------------------------------------------------
# TypeMetadata discriminated union tests
# ---------------------------------------------------------------------------


class TestTypeMetadata:
    def _task_with_metadata(self, task_type: TaskType, metadata):
        return TaskNode(
            id=_task_id(task_type),
            task_type=task_type,
            title="Test",
            description="Desc",
            created_at=NOW,
            updated_at=NOW,
            type_metadata=metadata,
        )

    def test_atomic_metadata(self):
        m = AtomicMetadata()
        assert m.task_type == TaskType.ATOMIC

    def test_delegated_metadata(self):
        m = DelegatedMetadata(assigned_resource_id="RES-abc-123")
        assert m.assigned_resource_id == "RES-abc-123"
        node = self._task_with_metadata(TaskType.DELEGATED, m)
        assert node.type_metadata.task_type == TaskType.DELEGATED  # type: ignore[union-attr]

    def test_followup_metadata(self):
        m = FollowUpMetadata(target_task_id="TSK-AB-1234-DEL", scheduled_fire_at=NOW)
        assert m.follow_up_count == 0

    def test_approval_metadata(self):
        m = ApprovalMetadata(approver_id="USER-abc-123", max_wait_days=7)
        assert m.max_wait_days == 7

    def test_composite_metadata_gate_defaults_to_and(self):
        m = CompositeMetadata(child_task_ids=["TSK-AB-1234-ATM"])
        assert m.completion_gate == GateType.AND

    def test_composite_metadata_or_gate(self):
        m = CompositeMetadata(child_task_ids=["TSK-AB-1234-ATM"], completion_gate=GateType.OR)
        assert m.completion_gate == GateType.OR

    def test_milestone_metadata(self):
        m = MilestoneMetadata(notifies=["RES-abc-123"], child_task_count=5)
        assert m.child_task_count == 5

    def test_review_metadata(self):
        m = ReviewMetadata(review_target_id="TSK-AB-1234-ATM")
        assert m.review_target_id == "TSK-AB-1234-ATM"

    def test_recurring_metadata(self):
        m = RecurringMetadata(cron_expression="0 9 * * 1-5", next_spawn_at=NOW)
        assert m.cron_expression == "0 9 * * 1-5"

    def test_decision_metadata(self):
        m = DecisionMetadata(options=["Option A", "Option B"], decision_deadline=NOW)
        assert len(m.options) == 2

    def test_checkin_metadata(self):
        m = CheckinMetadata(target_resource_id="RES-abc-123")
        assert m.task_type == TaskType.CHECKIN

    def test_research_metadata(self):
        m = ResearchMetadata(
            research_scope="Enough to define API contract", confidence_to_proceed=0.8
        )
        assert m.confidence_to_proceed == 0.8

    def test_discriminated_union_round_trip(self):
        """Serialise and re-parse via model_validate to confirm discriminator works."""
        m = DelegatedMetadata(assigned_resource_id="RES-abc-123")
        node = TaskNode(
            id=_task_id(TaskType.DELEGATED),
            task_type=TaskType.DELEGATED,
            title="Delegate me",
            description="Desc",
            created_at=NOW,
            updated_at=NOW,
            type_metadata=m,
        )
        data = node.model_dump()
        node2 = TaskNode.model_validate(data)
        assert node2.type_metadata.task_type == TaskType.DELEGATED  # type: ignore[union-attr]
        assert node2.type_metadata.assigned_resource_id == "RES-abc-123"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# UserNode tests
# ---------------------------------------------------------------------------


class TestUserNode:
    def test_user_node_defaults(self):
        node = UserNode(
            id=generate_user_id(),
            name="Alice",
            email="alice@example.com",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.timezone == "UTC"
        assert node.scoring_weights.W1_timeline == 0.25
        assert node.scoring_weights.W7_constraint == 0.05

    def test_user_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            UserNode(
                id="BAD",
                name="Bob",
                email="bob@example.com",
                created_at=NOW,
                updated_at=NOW,
            )

    def test_scoring_weights_sum_to_one(self):
        node = UserNode(
            id=generate_user_id(),
            name="Alice",
            email="alice@example.com",
            created_at=NOW,
            updated_at=NOW,
        )
        sw = node.scoring_weights
        total = (
            sw.W1_timeline
            + sw.W2_dependencies
            + sw.W3_critical_path
            + sw.W4_blocker
            + sw.W5_override
            + sw.W6_resource_risk
            + sw.W7_constraint
        )
        assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# GoalNode tests
# ---------------------------------------------------------------------------


class TestGoalNode:
    def test_goal_node_defaults(self):
        node = GoalNode(
            id=generate_goal_id(),
            title="Launch Q3 Feature",
            description="Ship the new payments feature by end of Q3",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.state == GoalState.ACTIVE
        assert node.priority == GoalPriority.P2
        assert node.progress.derived_percentage == 0.0

    def test_goal_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            GoalNode(
                id="BAD",
                title="X",
                description="X",
                created_at=NOW,
                updated_at=NOW,
            )

    def test_goal_with_p1_priority(self):
        node = GoalNode(
            id=generate_goal_id(),
            title="Critical goal",
            description="Must ship",
            priority=GoalPriority.P1,
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.priority == GoalPriority.P1


# ---------------------------------------------------------------------------
# ConstraintNode tests
# ---------------------------------------------------------------------------


class TestConstraintNode:
    def test_constraint_node_defaults(self):
        node = ConstraintNode(
            id=generate_constraint_id(),
            constraint_type=ConstraintType.DEADLINE,
            title="Q3 deadline",
            description="Must ship by 2026-09-30",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.applies_to == []
        assert node.rule.breached is False

    def test_constraint_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            ConstraintNode(
                id="BAD",
                constraint_type=ConstraintType.BUDGET,
                title="X",
                description="X",
                created_at=NOW,
                updated_at=NOW,
            )

    def test_constraint_rule_breach(self):
        rule = ConstraintRule(
            threshold="$50,000", current_value="$49,500", pressure_score=0.99, breached=False
        )
        node = ConstraintNode(
            id=generate_constraint_id(),
            constraint_type=ConstraintType.BUDGET,
            title="Budget cap",
            description="Cannot exceed $50k",
            rule=rule,
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.rule.pressure_score == 0.99


# ---------------------------------------------------------------------------
# ResourceNode tests
# ---------------------------------------------------------------------------


class TestResourceNode:
    def test_human_resource_defaults(self):
        node = ResourceNode(
            id=generate_resource_id(),
            resource_type=ResourceType.HUMAN,
            name="Bob Smith",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.reliability.overall_score == 0.8
        assert node.capacity.load_factor == 0.0

    def test_ai_agent_resource(self):
        node = ResourceNode(
            id=generate_resource_id(),
            resource_type=ResourceType.AI_AGENT,
            name="CodeAgent-v1",
            contact="https://api.agents.internal/code",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.resource_type == ResourceType.AI_AGENT
        assert node.contact == "https://api.agents.internal/code"

    def test_resource_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            ResourceNode(
                id="BAD",
                resource_type=ResourceType.HUMAN,
                name="X",
                created_at=NOW,
                updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# CheckinNode tests
# ---------------------------------------------------------------------------


class TestCheckinNode:
    def test_checkin_node_defaults(self):
        node = CheckinNode(
            id=f"CHK-{generate_edge_id()[5:]}",  # reuse uuid portion
            target_resource="RES-abc-123",
            task_refs=["TSK-AB-1234-DEL", "TSK-AB-5678-DEL"],
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.created_by == "AGENT"
        assert node.outbound_message is None


# ---------------------------------------------------------------------------
# HandoffNode tests
# ---------------------------------------------------------------------------


class TestHandoffNode:
    def test_handoff_node_defaults(self):
        node = HandoffNode(
            id=generate_handoff_node_id(),
            task_id="TSK-AB-1234-DEL",
            to_owner="agent-ops",
            created_at=NOW,
            updated_at=NOW,
        )
        assert node.task_id == "TSK-AB-1234-DEL"
        assert node.from_owner is None
        assert node.context_refs == []

    def test_handoff_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            HandoffNode(
                id="BAD",
                task_id="TSK-AB-1234-DEL",
                to_owner="agent-ops",
                created_at=NOW,
                updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# GraphEdge tests
# ---------------------------------------------------------------------------


class TestGraphEdge:
    def test_depends_on_edge(self):
        edge = GraphEdge(
            id=generate_edge_id(),
            edge_type=EdgeType.DEPENDS_ON,
            source_id="TSK-AB-1234-ATM",
            target_id="TSK-AB-5678-ATM",
            properties=EdgeProperties(gate_type=GateType.AND),
        )
        assert edge.properties.gate_type == GateType.AND

    def test_part_of_edge_with_sequence(self):
        edge = GraphEdge(
            id=generate_edge_id(),
            edge_type=EdgeType.PART_OF,
            source_id="TSK-AB-1234-ATM",
            target_id="GOAL-abc-123",
            properties=EdgeProperties(sequence_order=1),
        )
        assert edge.properties.sequence_order == 1

    def test_blocks_edge(self):
        from graphclaw.models.enums import EdgeStrength

        edge = GraphEdge(
            id=generate_edge_id(),
            edge_type=EdgeType.BLOCKS,
            source_id="TSK-AB-1234-ATM",
            target_id="TSK-AB-5678-ATM",
            properties=EdgeProperties(strength=EdgeStrength.HARD),
        )
        assert edge.properties.strength == EdgeStrength.HARD

    def test_follow_up_for_edge(self):
        edge = GraphEdge(
            id=generate_edge_id(),
            edge_type=EdgeType.FOLLOW_UP_FOR,
            source_id="TSK-AB-1234-FLW",
            target_id="TSK-AB-5678-DEL",
            properties=EdgeProperties(scheduled_fire_at=NOW),
        )
        assert edge.properties.scheduled_fire_at == NOW

    def test_edge_id_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            GraphEdge(
                id="BAD-ID",
                edge_type=EdgeType.DEPENDS_ON,
                source_id="TSK-AB-1234-ATM",
                target_id="TSK-AB-5678-ATM",
            )


# ---------------------------------------------------------------------------
# ScoreExplanation tests
# ---------------------------------------------------------------------------


class TestScoreExplanation:
    def _make_factor(self, name: str, raw: float, weight: float) -> ScoreFactor:
        return ScoreFactor(
            factor_name=name,
            raw_score=raw,
            weight=weight,
            weighted_score=raw * weight,
            plain_english=f"{name} contributes {raw * weight:.2f}",
        )

    def test_score_explanation_construction(self):
        task_id = _task_id(TaskType.ATOMIC)
        factors = [
            self._make_factor("timeline_urgency", 0.9, 0.25),
            self._make_factor("critical_path", 1.0, 0.20),
            self._make_factor("dependency_weight", 0.5, 0.20),
        ]
        modifier = ScoreModifier(
            modifier_type="critical_path_goal",
            multiplier=1.2,
            plain_english="On critical path for P1 goal",
        )
        explanation = ScoreExplanation(
            node_id=task_id,
            scored_at=NOW,
            final_score=0.87,
            rank=1,
            factors=factors,
            modifiers=[modifier],
            summary="Ranked #1 due to critical path and imminent deadline.",
            topology_note="First in a chain of 3 dependent tasks.",
        )
        assert explanation.rank == 1
        assert len(explanation.factors) == 3
        assert explanation.modifiers[0].multiplier == 1.2
        assert explanation.topology_note is not None

    def test_score_explanation_no_modifiers(self):
        task_id = _task_id(TaskType.ATOMIC)
        explanation = ScoreExplanation(
            node_id=task_id,
            scored_at=NOW,
            final_score=0.4,
            rank=5,
            factors=[self._make_factor("timeline_urgency", 0.4, 0.25)],
            summary="Mid-priority task.",
        )
        assert explanation.modifiers == []
        assert explanation.topology_note is None

    def test_action_queue_entry(self):
        task_id = _task_id(TaskType.ATOMIC)
        explanation = ScoreExplanation(
            node_id=task_id,
            scored_at=NOW,
            final_score=0.9,
            rank=1,
            factors=[self._make_factor("timeline_urgency", 0.9, 0.25)],
            summary="High priority.",
        )
        entry = ActionQueueEntry(
            node_id=task_id,
            final_score=0.9,
            rank=1,
            recommended_action="Send follow-up to Bob",
            autonomy_level=AutonomyLevel.SUGGEST,
            explanation=explanation,
            batched_with=[],
        )
        assert entry.rank == 1
        assert entry.autonomy_level == AutonomyLevel.SUGGEST
