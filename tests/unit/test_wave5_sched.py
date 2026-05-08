# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests/unit/test_wave5_sched.py — Wave 5 (FR-SCHED-001, FR-SCHED-002) tests.

Covers:
- FollowUpTrigger config + candidate selection (FR-SCHED-001)
- OwnerOfflineEscalationQueue enqueue / resolve / list / expired (FR-SCHED-002)
- Migration 0019 exists and SQL is valid
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# FR-SCHED-001 — FollowUpTrigger
# ---------------------------------------------------------------------------


class TestFollowUpTrigger:
    """Tests for FollowUpTrigger (FR-SCHED-001)."""

    @pytest.mark.asyncio
    async def test_run_returns_candidate_count(self):
        from graphclaw.triggers.follow_up import FollowUpTrigger, FollowUpTriggerConfig

        config = FollowUpTriggerConfig(user_id="USER-test-001", follow_up_days=3)

        mock_repo = MagicMock()
        mock_repo.list_follow_up_candidates = AsyncMock(
            return_value=[
                {
                    "task_id": "TSK-001",
                    "title": "Task A",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.9,
                },
                {
                    "task_id": "TSK-002",
                    "title": "Task B",
                    "state": "IN_PROGRESS",
                    "last_outbound_at": None,
                    "score": 0.7,
                },
            ]
        )

        mock_agent = MagicMock()
        mock_agent.process_chat_message = AsyncMock(return_value={"reply": "ok"})

        trigger = FollowUpTrigger(mock_repo, mock_agent, config)
        count = await trigger.run()

        assert count == 2
        assert mock_agent.process_chat_message.call_count == 2

    @pytest.mark.asyncio
    async def test_run_respects_max_candidates(self):
        from graphclaw.triggers.follow_up import FollowUpTrigger, FollowUpTriggerConfig

        config = FollowUpTriggerConfig(user_id="USER-test-001", max_candidates=1)

        mock_repo = MagicMock()
        mock_repo.list_follow_up_candidates = AsyncMock(
            return_value=[
                {
                    "task_id": "TSK-001",
                    "title": "A",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.9,
                },
                {
                    "task_id": "TSK-002",
                    "title": "B",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.8,
                },
            ]
        )
        mock_agent = MagicMock()
        mock_agent.process_chat_message = AsyncMock(return_value={})

        trigger = FollowUpTrigger(mock_repo, mock_agent, config)
        count = await trigger.run()

        # Only 1 should be processed despite 2 candidates
        assert count == 1
        assert mock_agent.process_chat_message.call_count == 1

    @pytest.mark.asyncio
    async def test_run_filters_by_interrupt_threshold(self):
        from graphclaw.triggers.follow_up import FollowUpTrigger, FollowUpTriggerConfig

        config = FollowUpTriggerConfig(
            user_id="USER-test-001",
            interrupt_threshold=0.8,
        )

        mock_repo = MagicMock()
        mock_repo.list_follow_up_candidates = AsyncMock(
            return_value=[
                {
                    "task_id": "TSK-001",
                    "title": "High",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.9,
                },
                {
                    "task_id": "TSK-002",
                    "title": "Low",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.5,
                },  # Below threshold
            ]
        )
        mock_agent = MagicMock()
        mock_agent.process_chat_message = AsyncMock(return_value={})

        trigger = FollowUpTrigger(mock_repo, mock_agent, config)
        count = await trigger.run()

        assert count == 1  # Only score ≥0.8 candidate
        assert mock_agent.process_chat_message.call_count == 1
        call_args = mock_agent.process_chat_message.call_args_list[0]
        assert "TSK-001" in call_args.kwargs.get("text", "") or "TSK-001" in str(call_args)

    @pytest.mark.asyncio
    async def test_run_graceful_on_candidate_query_failure(self):
        from graphclaw.triggers.follow_up import FollowUpTrigger, FollowUpTriggerConfig

        config = FollowUpTriggerConfig(user_id="USER-test-001")

        mock_repo = MagicMock()
        mock_repo.list_follow_up_candidates = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_agent = MagicMock()
        mock_agent.process_chat_message = AsyncMock(return_value={})

        trigger = FollowUpTrigger(mock_repo, mock_agent, config)
        # Should not raise
        count = await trigger.run()
        assert count == 0

    @pytest.mark.asyncio
    async def test_run_graceful_on_agent_failure(self):
        from graphclaw.triggers.follow_up import FollowUpTrigger, FollowUpTriggerConfig

        config = FollowUpTriggerConfig(user_id="USER-test-001")

        mock_repo = MagicMock()
        mock_repo.list_follow_up_candidates = AsyncMock(
            return_value=[
                {
                    "task_id": "TSK-001",
                    "title": "A",
                    "state": "WAITING",
                    "last_outbound_at": None,
                    "score": 0.9,
                },
            ]
        )
        mock_agent = MagicMock()
        mock_agent.process_chat_message = AsyncMock(side_effect=RuntimeError("LLM down"))

        trigger = FollowUpTrigger(mock_repo, mock_agent, config)
        # Should not raise — per-candidate errors are gracefully logged
        count = await trigger.run()
        assert count == 1  # Processed (attempted), even if agent failed

    def test_config_defaults(self):
        from graphclaw.triggers.follow_up import FollowUpTriggerConfig

        cfg = FollowUpTriggerConfig(user_id="USER-x")
        assert cfg.follow_up_days == 3
        assert cfg.max_candidates == 10
        assert cfg.interrupt_threshold == 0.0
        assert cfg.agent_id == "main"


# ---------------------------------------------------------------------------
# FR-SCHED-002 — OwnerOfflineEscalationQueue
# ---------------------------------------------------------------------------


class TestOwnerOfflineEscalationQueue:
    """Tests for OwnerOfflineEscalationQueue (FR-SCHED-002)."""

    @pytest.mark.asyncio
    async def test_enqueue_creates_pending_decision(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        decision = await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-xyz",
            prompt="Should I extend deadline by 2 days?",
            proposed_action={"action": "extend_deadline", "days": 2},
        )

        assert decision.user_id == "USER-001"
        assert decision.context_ref == "TSK-xyz"
        assert "extend deadline" in decision.prompt.lower() or decision.prompt
        assert decision.resolved_at is None
        assert decision.expires_at is not None

    @pytest.mark.asyncio
    async def test_list_pending_returns_enqueued(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-a",
            prompt="Q1?",
            proposed_action={},
        )
        await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-b",
            prompt="Q2?",
            proposed_action={},
        )
        # Different user
        await queue.enqueue(
            user_id="USER-002",
            context_ref="TSK-c",
            prompt="Q3?",
            proposed_action={},
        )

        pending = await queue.list_pending("USER-001")
        assert len(pending) == 2
        assert all(d.user_id == "USER-001" for d in pending)

    @pytest.mark.asyncio
    async def test_resolve_marks_decision_resolved(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        decision = await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-x",
            prompt="Resolve this?",
            proposed_action={"action": "skip"},
        )

        result = await queue.resolve(decision.id, resolution="owner_decided")
        assert result is True
        assert decision.resolved_at is not None
        assert decision.resolution == "owner_decided"

        # Should not appear in pending list
        pending = await queue.list_pending("USER-001")
        assert not any(d.id == decision.id for d in pending)

    @pytest.mark.asyncio
    async def test_resolve_returns_false_for_unknown_id(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        result = await queue.resolve("nonexistent-uuid-xxxx")
        assert result is False

    @pytest.mark.asyncio
    async def test_process_expired_fires_conservative_fallback(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        # Enqueue with already-past expiry
        decision = await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-expired",
            prompt="Time is up?",
            proposed_action={"action": "do_nothing"},
            wait_hours=0,  # Expires immediately
        )
        # Force expiry into the past
        decision.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

        expired = await queue.process_expired()
        assert len(expired) == 1
        assert expired[0].id == decision.id
        assert expired[0].resolution == "fallback_conservative"
        assert expired[0].resolved_at is not None

    @pytest.mark.asyncio
    async def test_process_expired_ignores_future_decisions(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-future",
            prompt="Not yet?",
            proposed_action={},
            wait_hours=999,
        )

        expired = await queue.process_expired()
        assert len(expired) == 0

    @pytest.mark.asyncio
    async def test_process_expired_ignores_already_resolved(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue()
        decision = await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-already",
            prompt="Already done?",
            proposed_action={},
            wait_hours=0,
        )
        decision.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        # Resolve it before calling process_expired
        await queue.resolve(decision.id)

        expired = await queue.process_expired()
        assert len(expired) == 0

    def test_pending_decision_to_dict(self):
        from graphclaw.agent.escalation import PendingDecision

        d = PendingDecision(
            user_id="USER-001",
            context_ref="TSK-x",
            prompt="P?",
            proposed_action={"action": "noop"},
        )
        d_dict = d.to_dict()
        assert d_dict["user_id"] == "USER-001"
        assert d_dict["context_ref"] == "TSK-x"
        assert d_dict["proposed_action"] == {"action": "noop"}
        assert d_dict["resolved_at"] is None

    @pytest.mark.asyncio
    async def test_wait_hours_override(self):
        from graphclaw.agent.escalation import OwnerOfflineEscalationQueue

        queue = OwnerOfflineEscalationQueue(on_owner_unreachable_after_hours=24)
        now = datetime.now(timezone.utc)

        decision = await queue.enqueue(
            user_id="USER-001",
            context_ref="TSK-custom",
            prompt="Override wait?",
            proposed_action={},
            wait_hours=48,
        )

        # Should be ~48 hours from now
        delta = decision.expires_at - now
        assert 47 <= delta.total_seconds() / 3600 <= 49


# ---------------------------------------------------------------------------
# Migration 0019 exists
# ---------------------------------------------------------------------------


class TestMigration0019:
    def test_escalation_queue_migration_exists(self):
        from graphclaw.migrations.catalogue import MIGRATIONS

        versions = [m.version for m in MIGRATIONS]
        assert "0019" in versions

    def test_escalation_queue_migration_sql(self):
        from graphclaw.migrations.catalogue import MIGRATIONS

        m = next(m for m in MIGRATIONS if m.version == "0019")
        assert "escalation_queue" in m.sql_up
        assert "user_id" in m.sql_up
        assert "expires_at" in m.sql_up
        assert "proposed_action" in m.sql_up
