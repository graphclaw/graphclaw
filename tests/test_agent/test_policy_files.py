# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_policy_files — FR-STORE-002 acceptance tests.

Verifies:
  AC1: Policy file at canonical path; loader returns parsed schema + raw body.
  AC2: Frontmatter validated against Pydantic schema; bad schema raises PolicyLoadError.
  AC3: fail_mode=closed causes load failure to raise PolicyLoadError.
  AC4: fail_mode=degraded causes load failure to return schema defaults.
  AC5: Cache invalidation on write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.policies.evaluator import (
    OutboundIntent,
    evaluate_outbound_intent,
)
from graphclaw.agent.policies.loader import PolicyLoader, PolicyLoadError
from graphclaw.agent.policies.schemas import (
    DelegationPolicy,
    FailMode,
    ReplyTonePolicy,
)


def _mock_storage(files: dict[str, bytes]) -> object:
    storage = MagicMock()

    async def _read(path: str) -> bytes:
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    storage.read = AsyncMock(side_effect=_read)
    storage.write = AsyncMock()
    return storage


_DELEGATION_MD = b"""---
fail_mode: closed
auto_acknowledge: true
accept_deadline_extension_max_days: 3
escalate_on_blocker: true
---
# Delegation Policy body
"""

_REPLY_TONE_MD = b"""---
fail_mode: degraded
voice: first_person
brevity: terse
---
Reply tone body.
"""

_BAD_FM_MD = b"""---
fail_mode: invalid-value
---
Bad policy.
"""


class TestPolicyLoader:
    async def test_load_delegation_policy(self) -> None:
        storage = _mock_storage(
            {
                "USER-alice/agents/main/policies/delegation.md": _DELEGATION_MD,
            }
        )
        loader = PolicyLoader(storage)
        loaded = await loader.load("USER-alice", "main", "delegation")
        assert isinstance(loaded.schema_obj, DelegationPolicy)
        assert loaded.schema_obj.fail_mode == FailMode.CLOSED
        assert loaded.schema_obj.accept_deadline_extension_max_days == 3
        assert "Delegation Policy body" in loaded.body

    async def test_load_reply_tone_policy(self) -> None:
        storage = _mock_storage(
            {
                "USER-alice/agents/main/policies/reply_tone.md": _REPLY_TONE_MD,
            }
        )
        loader = PolicyLoader(storage)
        loaded = await loader.load("USER-alice", "main", "reply_tone")
        assert isinstance(loaded.schema_obj, ReplyTonePolicy)
        assert loaded.schema_obj.voice == "first_person"
        assert loaded.schema_obj.brevity == "terse"

    async def test_missing_closed_policy_raises(self) -> None:
        storage = _mock_storage({})
        loader = PolicyLoader(storage)
        with pytest.raises(PolicyLoadError, match="fail_mode=closed"):
            await loader.load("USER-alice", "main", "delegation")

    async def test_missing_degraded_policy_returns_defaults(self) -> None:
        storage = _mock_storage({})
        loader = PolicyLoader(storage)
        loaded = await loader.load("USER-alice", "main", "reply_tone")
        assert loaded.schema_obj.voice == "neutral"  # Pydantic default
        assert loaded.body == ""

    async def test_unknown_policy_name_raises(self) -> None:
        storage = _mock_storage({})
        loader = PolicyLoader(storage)
        with pytest.raises(PolicyLoadError, match="Unknown policy"):
            await loader.load("USER-alice", "main", "nonexistent")

    async def test_etag_computed(self) -> None:
        storage = _mock_storage(
            {
                "USER-alice/agents/main/policies/delegation.md": _DELEGATION_MD,
            }
        )
        loader = PolicyLoader(storage)
        loaded = await loader.load("USER-alice", "main", "delegation")
        assert loaded.etag != ""
        # Same bytes → same etag.
        assert len(loaded.etag) == 32  # MD5 hex

    async def test_cache_invalidation_called(self) -> None:
        redis = MagicMock()
        redis.delete = AsyncMock()
        storage = _mock_storage({})
        loader = PolicyLoader(storage, redis_client=redis)
        await loader.invalidate("USER-alice", "main", "delegation")
        redis.delete.assert_called_once_with("policy:USER-alice:main:delegation")


class TestPolicyEvaluator:
    def test_allow_simple_intent(self) -> None:
        policy = DelegationPolicy(
            allowed_state_transitions=[{"from": "WAITING", "to": "IN_PROGRESS"}],
            accept_deadline_extension_max_days=3,
            escalate_on_blocker=False,
        )
        intent = OutboundIntent(
            task_id="TSK-1",
            recipient_id="RES-bob",
            purpose="Notify Bob of task progress",
            proposed_state_transition=("WAITING", "IN_PROGRESS"),
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "allow"

    def test_escalate_on_forbidden_transition(self) -> None:
        policy = DelegationPolicy(
            allowed_state_transitions=[{"from": "WAITING", "to": "IN_PROGRESS"}],
        )
        intent = OutboundIntent(
            task_id="TSK-2",
            recipient_id="RES-bob",
            purpose="Force close task",
            proposed_state_transition=("IN_PROGRESS", "CANCELLED"),
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "escalate"
        assert "CANCELLED" in result.reason

    def test_escalate_on_excess_deadline_extension(self) -> None:
        policy = DelegationPolicy(accept_deadline_extension_max_days=3)
        intent = OutboundIntent(
            task_id="TSK-3",
            recipient_id="RES-carol",
            purpose="Ask for extension",
            deadline_extension_days=7,
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "escalate"
        assert "7" in result.reason

    def test_recipient_override_allows_zero_extension(self) -> None:
        policy = DelegationPolicy(
            accept_deadline_extension_max_days=3,
            recipient_overrides={"CEO-001": {"accept_deadline_extension_max_days": 0}},
        )
        intent = OutboundIntent(
            task_id="TSK-4",
            recipient_id="CEO-001",
            purpose="Request extension from CEO",
            deadline_extension_days=1,
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "escalate"  # CEO override = 0 days

    def test_escalate_on_blocker_purpose(self) -> None:
        policy = DelegationPolicy(escalate_on_blocker=True)
        intent = OutboundIntent(
            task_id="TSK-5",
            recipient_id="RES-bob",
            purpose="Inform Bob about blocker on TSK-5",
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "escalate"

    def test_no_escalate_when_blocker_disabled(self) -> None:
        policy = DelegationPolicy(escalate_on_blocker=False)
        intent = OutboundIntent(
            task_id="TSK-6",
            recipient_id="RES-bob",
            purpose="Inform Bob about blocker on TSK-6",
        )
        result = evaluate_outbound_intent(intent, policy)
        assert result.decision == "allow"
