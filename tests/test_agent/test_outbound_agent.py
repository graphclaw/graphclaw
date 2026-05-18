# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_outbound_agent — Wave 2 unit tests (FR-OUT-001..004).

Tests OutboundCommunicationAgent:
  FR-OUT-001: send() orchestrates policy → channel resolve → dispatch.
  FR-OUT-002: Channel resolved from preferences; stickiness override applied.
  FR-OUT-003: Delegation policy enforces escalation.
  FR-OUT-004: CheckinNode + reply-key + intelligence write post-dispatch.
"""

from __future__ import annotations

import pytest

from graphclaw.agent.outbound import DispatchResult, OutboundCommunicationAgent
from graphclaw.agent.outbound_intent import OutboundIntent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDispatcher:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, channel: str, *, to: str, subject: str = "", body: str) -> None:
        self.sent.append({"channel": channel, "to": to, "subject": subject, "body": body})


class FakeGraphStore:
    def __init__(self, recipient_prefs: dict | None = None) -> None:
        self._prefs = recipient_prefs or {}
        self.checkins_created: list[str] = []
        self.intelligence_lines: list[str] = []

    async def get_node(
        self, node_id: str, include_archived: bool = False, caller_context=None
    ) -> dict | None:
        if node_id == "RES-bob":
            return {
                "id": "RES-bob",
                "preferences": self._prefs.get("RES-bob", {"preferred_channel": "email"}),
                "identities": {"emails": ["bob@example.com"], "telegram_id": "12345"},
            }
        return None

    async def get_active_thread(self, recipient_id: str, since_iso: str) -> dict | None:
        return None  # no stickiness by default

    async def create_checkin_node(
        self, *, task_id: str, outbound_message: str, channel: str, agent_id: str, recipient: str
    ) -> str:
        cid = f"CHK-{len(self.checkins_created) + 1:03}"
        self.checkins_created.append(cid)
        return cid

    async def update_node_intelligence(self, node_id: str, intelligence_text: str) -> None:
        self.intelligence_lines.append(f"{node_id}:{intelligence_text}")


class FakeReplyKeyStore:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    async def write(self, record, msg_id: str) -> None:
        self.writes.append({"record": record, "msg_id": msg_id})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(
    *,
    dispatcher=None,
    graph_store=None,
    policy_loader=None,
    reply_key_store=None,
    owner="USER-owner",
) -> OutboundCommunicationAgent:
    return OutboundCommunicationAgent(
        dispatcher=dispatcher or FakeDispatcher(),
        graph_store=graph_store,
        policy_loader=policy_loader,
        reply_key_store=reply_key_store,
        owner_user_id=owner,
        agent_id=owner,
    )


# ---------------------------------------------------------------------------
# FR-OUT-001: Orchestration flow
# ---------------------------------------------------------------------------


class TestOutboundCommunicationAgentOrchestration:
    @pytest.mark.asyncio
    async def test_send_dispatches_message(self) -> None:
        dispatcher = FakeDispatcher()
        agent = make_agent(dispatcher=dispatcher)
        intent = OutboundIntent(recipient_id="RES-bob", purpose="follow up on task")
        result = await agent.send(intent)
        assert not result.escalated
        assert len(dispatcher.sent) == 1

    @pytest.mark.asyncio
    async def test_send_returns_dispatch_result(self) -> None:
        agent = make_agent()
        intent = OutboundIntent(recipient_id="RES-bob", purpose="check in")
        result = await agent.send(intent)
        assert isinstance(result, DispatchResult)
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_type_error_on_wrong_intent(self) -> None:
        agent = make_agent()
        with pytest.raises(TypeError):
            await agent.send("not an intent")


# ---------------------------------------------------------------------------
# FR-OUT-002: Channel resolution
# ---------------------------------------------------------------------------


class TestChannelResolution:
    @pytest.mark.asyncio
    async def test_defaults_to_email_when_no_prefs(self) -> None:
        dispatcher = FakeDispatcher()
        agent = make_agent(dispatcher=dispatcher)
        intent = OutboundIntent(recipient_id="RES-unknown", purpose="hello")
        await agent.send(intent)
        assert dispatcher.sent[0]["channel"] == "email"

    @pytest.mark.asyncio
    async def test_uses_telegram_from_preferences(self) -> None:
        dispatcher = FakeDispatcher()
        graph = FakeGraphStore(recipient_prefs={"RES-bob": {"preferred_channel": "telegram"}})
        agent = make_agent(dispatcher=dispatcher, graph_store=graph)
        intent = OutboundIntent(recipient_id="RES-bob", purpose="hello via telegram")
        await agent.send(intent)
        assert dispatcher.sent[0]["channel"] == "telegram"
        assert dispatcher.sent[0]["to"] == "12345"  # telegram_id from identities

    @pytest.mark.asyncio
    async def test_channel_override_takes_precedence(self) -> None:
        dispatcher = FakeDispatcher()
        graph = FakeGraphStore(recipient_prefs={"RES-bob": {"preferred_channel": "telegram"}})
        agent = make_agent(dispatcher=dispatcher, graph_store=graph)
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="forced email",
            channel_override="email",
        )
        await agent.send(intent)
        assert dispatcher.sent[0]["channel"] == "email"

    @pytest.mark.asyncio
    async def test_stickiness_overrides_preference(self) -> None:
        dispatcher = FakeDispatcher()

        class StickyGraphStore(FakeGraphStore):
            async def get_active_thread(self, recipient_id, since_iso):
                return {"channel": "telegram", "thread_id": "TG-THREAD-1"}

        graph = StickyGraphStore(recipient_prefs={"RES-bob": {"preferred_channel": "email"}})
        agent = make_agent(dispatcher=dispatcher, graph_store=graph)
        intent = OutboundIntent(recipient_id="RES-bob", purpose="sticky")
        result = await agent.send(intent)
        assert dispatcher.sent[0]["channel"] == "telegram"
        assert result.channel == "telegram"
        assert result.thread_id == "TG-THREAD-1"


# ---------------------------------------------------------------------------
# FR-OUT-003: Delegation policy enforcement
# ---------------------------------------------------------------------------


class TestDelegationPolicyEnforcement:
    @pytest.mark.asyncio
    async def test_escalation_blocks_dispatch(self) -> None:
        dispatcher = FakeDispatcher()

        class EscalatingLoader:
            async def load(self, user_id, agent_id, policy_name):
                from types import SimpleNamespace

                from graphclaw.agent.policies.schemas import DelegationPolicy

                return SimpleNamespace(
                    schema_obj=DelegationPolicy(
                        fail_mode="closed",
                        accept_deadline_extension_max_days=0,
                        escalate_on_blocker=True,
                    ),
                    body="",
                )

        agent = make_agent(dispatcher=dispatcher, policy_loader=EscalatingLoader())
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="extend deadline",
            deadline_extension_days=10,  # exceeds policy max=0
        )
        result = await agent.send(intent)
        assert result.escalated
        assert len(dispatcher.sent) == 0  # no dispatch

    @pytest.mark.asyncio
    async def test_allow_does_not_block_dispatch(self) -> None:
        dispatcher = FakeDispatcher()

        class AllowingLoader:
            async def load(self, user_id, agent_id, policy_name):
                from types import SimpleNamespace

                from graphclaw.agent.policies.schemas import DelegationPolicy

                return SimpleNamespace(
                    schema_obj=DelegationPolicy(
                        fail_mode="closed",
                        accept_deadline_extension_max_days=7,
                        escalate_on_blocker=False,
                    ),
                    body="",
                )

        agent = make_agent(dispatcher=dispatcher, policy_loader=AllowingLoader())
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="extend deadline",
            deadline_extension_days=5,  # within policy max=7
        )
        result = await agent.send(intent)
        assert not result.escalated
        assert len(dispatcher.sent) == 1

    @pytest.mark.asyncio
    async def test_policy_load_error_does_not_block(self) -> None:
        """PolicyLoader failure should NOT block dispatch (fail-open at agent level)."""
        dispatcher = FakeDispatcher()

        class FailingLoader:
            async def load(self, user_id, agent_id, policy_name):
                raise RuntimeError("loader failure")

        agent = make_agent(dispatcher=dispatcher, policy_loader=FailingLoader())
        intent = OutboundIntent(recipient_id="RES-bob", purpose="hello")
        result = await agent.send(intent)
        assert not result.escalated
        assert len(dispatcher.sent) == 1


# ---------------------------------------------------------------------------
# FR-OUT-004: CheckinNode + reply-key + intelligence write
# ---------------------------------------------------------------------------


class TestPostDispatchHooks:
    @pytest.mark.asyncio
    async def test_checkin_node_created_when_task_id_present(self) -> None:
        graph = FakeGraphStore()
        agent = make_agent(graph_store=graph)
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="follow up",
            task_id="TSK-001",
        )
        result = await agent.send(intent)
        assert result.checkin_id is not None
        assert len(graph.checkins_created) == 1

    @pytest.mark.asyncio
    async def test_no_checkin_when_no_task_id(self) -> None:
        graph = FakeGraphStore()
        agent = make_agent(graph_store=graph)
        intent = OutboundIntent(recipient_id="RES-bob", purpose="standalone message")
        result = await agent.send(intent)
        assert result.checkin_id is None
        assert len(graph.checkins_created) == 0

    @pytest.mark.asyncio
    async def test_reply_key_written_after_dispatch(self) -> None:
        graph = FakeGraphStore()
        reply_store = FakeReplyKeyStore()
        agent = make_agent(graph_store=graph, reply_key_store=reply_store)
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="follow up",
            task_id="TSK-001",
        )
        await agent.send(intent)
        assert len(reply_store.writes) == 1
        assert reply_store.writes[0]["record"].counterparty_id == "RES-bob"

    @pytest.mark.asyncio
    async def test_intelligence_appended_to_task(self) -> None:
        graph = FakeGraphStore()
        agent = make_agent(graph_store=graph)
        intent = OutboundIntent(
            recipient_id="RES-bob",
            purpose="update on project",
            task_id="TSK-999",
        )
        await agent.send(intent)
        assert any("TSK-999" in line for line in graph.intelligence_lines)

    @pytest.mark.asyncio
    async def test_no_intelligence_write_when_no_task_id(self) -> None:
        graph = FakeGraphStore()
        agent = make_agent(graph_store=graph)
        intent = OutboundIntent(recipient_id="RES-bob", purpose="no task")
        await agent.send(intent)
        assert len(graph.intelligence_lines) == 0
