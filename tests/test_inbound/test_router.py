# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for FR-IN-001: InboundRouter message classification.

FR-IN-001: Classify inbound messages into route decisions.
FR-IN-002: Counterparty resolver integration.
FR-IN-003: AgentChannelIdentityRegistry.
"""

from __future__ import annotations

import pytest

from graphclaw.gateway.agent_channel_identity import AgentChannelIdentityRegistry
from graphclaw.inbound.router import InboundRoute, InboundRouter
from graphclaw.models.agent_channel_identity import AgentChannelIdentity

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Fake AgentChannelIdentityRegistry for router tests."""

    def __init__(self, entries: list[AgentChannelIdentity]) -> None:
        self._entries = {(e.channel, e.account_id): e for e in entries}

    async def lookup(self, *, channel: str, account_id: str) -> AgentChannelIdentity | None:
        entry = self._entries.get((channel, account_id))
        if entry is None or not entry.active:
            return None
        return entry

    async def is_owner_identity(self, *, user_id: str, channel: str, sender_id: str) -> bool:
        for e in self._entries.values():
            if e.user_id != user_id or e.channel != channel:
                continue
            if sender_id in e.owner_identities:
                return True
        return False


class FakeResolver:
    """Fake counterparty resolver for router tests."""

    def __init__(self, mapping: dict[tuple[str, str], str]) -> None:
        self._mapping = mapping

    async def resolve_to_node(
        self,
        channel: str,
        sender_id: str,
        owner_user_id: str,  # noqa: ARG002
    ) -> str | None:
        return self._mapping.get((channel, sender_id))


class FakeReplyKeyStore:
    """Fake reply-key store for router tests."""

    def __init__(self, hits: set[tuple[str, str]]) -> None:
        self._hits = hits

    async def read_from_redis(
        self,
        channel: str,
        thread_id: str,
        msg_id: str,  # noqa: ARG002
    ) -> object | None:
        if (channel, thread_id) in self._hits:
            return object()  # truthy
        return None

    async def read_from_db(self, channel: str, thread_id: str) -> object | None:
        return None


# ---------------------------------------------------------------------------
# FR-IN-001: Routing matrix
# ---------------------------------------------------------------------------


class TestRoutingMatrix:
    """Verify the five-route routing matrix from FR-IN-001."""

    @pytest.fixture()
    def bot_entry(self) -> AgentChannelIdentity:
        return AgentChannelIdentity(
            user_id="USER-001",
            agent_id="AGENT-001",
            channel="telegram",
            account_id="bot123",
            owner_identities=["owner_tg_id"],
            active=True,
        )

    @pytest.fixture()
    def registry(self, bot_entry: AgentChannelIdentity) -> FakeRegistry:
        return FakeRegistry([bot_entry])

    @pytest.fixture()
    def resolver_with_counterparty(self) -> FakeResolver:
        return FakeResolver({("telegram", "cp_tg_id"): "RES-counterparty-001"})

    @pytest.fixture()
    def resolver_empty(self) -> FakeResolver:
        return FakeResolver({})

    # ── drop: receiving account not mapped ───────────────────────────────

    @pytest.mark.asyncio
    async def test_drop_when_no_registry(self) -> None:
        router = InboundRouter()  # no registry
        decision = await router.classify(
            channel="telegram", sender_id="anyone", receiving_account="unknown_bot"
        )
        assert decision.route == InboundRoute.DROP

    @pytest.mark.asyncio
    async def test_drop_unknown_receiving_account(self, registry: FakeRegistry) -> None:
        router = InboundRouter(channel_registry=registry)
        decision = await router.classify(
            channel="telegram", sender_id="anyone", receiving_account="not_registered"
        )
        assert decision.route == InboundRoute.DROP

    @pytest.mark.asyncio
    async def test_drop_inactive_entry(self, bot_entry: AgentChannelIdentity) -> None:
        inactive = bot_entry.model_copy(update={"active": False})
        registry = FakeRegistry([inactive])
        router = InboundRouter(channel_registry=registry)
        decision = await router.classify(
            channel="telegram", sender_id="anyone", receiving_account="bot123"
        )
        assert decision.route == InboundRoute.DROP

    # ── user_chat: sender is owner ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_user_chat_when_sender_is_owner(self, registry: FakeRegistry) -> None:
        router = InboundRouter(channel_registry=registry)
        decision = await router.classify(
            channel="telegram",
            sender_id="owner_tg_id",
            receiving_account="bot123",
        )
        assert decision.route == InboundRoute.USER_CHAT
        assert decision.owner_user_id == "USER-001"
        assert decision.agent_id == "AGENT-001"

    # ── unknown_party: sender unknown ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unknown_party_when_no_resolver(self, registry: FakeRegistry) -> None:
        router = InboundRouter(channel_registry=registry)  # no resolver
        decision = await router.classify(
            channel="telegram", sender_id="stranger", receiving_account="bot123"
        )
        assert decision.route == InboundRoute.UNKNOWN_PARTY
        assert decision.owner_user_id == "USER-001"

    @pytest.mark.asyncio
    async def test_unknown_party_when_resolver_returns_none(
        self, registry: FakeRegistry, resolver_empty: FakeResolver
    ) -> None:
        router = InboundRouter(channel_registry=registry, counterparty_resolver=resolver_empty)
        decision = await router.classify(
            channel="telegram", sender_id="stranger", receiving_account="bot123"
        )
        assert decision.route == InboundRoute.UNKNOWN_PARTY

    # ── counterparty_reply: known + reply-key hit ─────────────────────────

    @pytest.mark.asyncio
    async def test_counterparty_reply_with_reply_key(
        self,
        registry: FakeRegistry,
        resolver_with_counterparty: FakeResolver,
    ) -> None:
        reply_keys = FakeReplyKeyStore(hits={("telegram", "thread-abc")})
        router = InboundRouter(
            channel_registry=registry,
            counterparty_resolver=resolver_with_counterparty,
            reply_key_store=reply_keys,
        )
        decision = await router.classify(
            channel="telegram",
            sender_id="cp_tg_id",
            receiving_account="bot123",
            thread_id="thread-abc",
        )
        assert decision.route == InboundRoute.COUNTERPARTY_REPLY
        assert decision.counterparty_node_id == "RES-counterparty-001"

    # ── counterparty_proactive: known + no reply-key ──────────────────────

    @pytest.mark.asyncio
    async def test_counterparty_proactive_no_reply_key(
        self,
        registry: FakeRegistry,
        resolver_with_counterparty: FakeResolver,
    ) -> None:
        reply_keys = FakeReplyKeyStore(hits=set())
        router = InboundRouter(
            channel_registry=registry,
            counterparty_resolver=resolver_with_counterparty,
            reply_key_store=reply_keys,
        )
        decision = await router.classify(
            channel="telegram",
            sender_id="cp_tg_id",
            receiving_account="bot123",
            thread_id="thread-xyz",
        )
        assert decision.route == InboundRoute.COUNTERPARTY_PROACTIVE

    @pytest.mark.asyncio
    async def test_counterparty_proactive_when_no_reply_key_store(
        self,
        registry: FakeRegistry,
        resolver_with_counterparty: FakeResolver,
    ) -> None:
        router = InboundRouter(
            channel_registry=registry,
            counterparty_resolver=resolver_with_counterparty,
        )
        decision = await router.classify(
            channel="telegram",
            sender_id="cp_tg_id",
            receiving_account="bot123",
        )
        assert decision.route == InboundRoute.COUNTERPARTY_PROACTIVE

    # ── RouteDecision fields populated correctly ──────────────────────────

    @pytest.mark.asyncio
    async def test_route_decision_has_channel(self, registry: FakeRegistry) -> None:
        router = InboundRouter(channel_registry=registry)
        decision = await router.classify(
            channel="telegram", sender_id="owner_tg_id", receiving_account="bot123"
        )
        assert decision.channel == "telegram"

    @pytest.mark.asyncio
    async def test_route_decision_has_thread_id(self, registry: FakeRegistry) -> None:
        router = InboundRouter(channel_registry=registry)
        decision = await router.classify(
            channel="telegram",
            sender_id="owner_tg_id",
            receiving_account="bot123",
            thread_id="thr-99",
        )
        assert decision.thread_id == "thr-99"

    @pytest.mark.asyncio
    async def test_drop_has_empty_owner(self) -> None:
        router = InboundRouter()
        decision = await router.classify(channel="telegram", sender_id="x", receiving_account="y")
        assert decision.route == InboundRoute.DROP
        assert decision.owner_user_id == ""


# ---------------------------------------------------------------------------
# FR-IN-003: AgentChannelIdentityRegistry
# ---------------------------------------------------------------------------


class TestAgentChannelIdentityRegistry:
    """Unit tests for the in-memory registry."""

    def _entry(self, channel="telegram", account_id="bot1", user_id="U1", active=True):
        return AgentChannelIdentity(
            user_id=user_id,
            agent_id="A1",
            channel=channel,
            account_id=account_id,
            owner_identities=["owner_id"],
            active=active,
        )

    def test_empty_registry_returns_none(self) -> None:
        reg = AgentChannelIdentityRegistry()
        import asyncio

        result = asyncio.run(reg.lookup(channel="telegram", account_id="missing"))
        assert result is None

    def test_lookup_returns_entry(self) -> None:
        entry = self._entry()
        reg = AgentChannelIdentityRegistry([entry])
        import asyncio

        result = asyncio.run(reg.lookup(channel="telegram", account_id="bot1"))
        assert result is not None
        assert result.user_id == "U1"

    def test_lookup_inactive_returns_none(self) -> None:
        entry = self._entry(active=False)
        reg = AgentChannelIdentityRegistry([entry])
        import asyncio

        result = asyncio.run(reg.lookup(channel="telegram", account_id="bot1"))
        assert result is None

    def test_add_hot_reload(self) -> None:
        reg = AgentChannelIdentityRegistry()
        entry = self._entry()
        reg.add(entry)
        assert len(reg) == 1

    def test_remove_entry(self) -> None:
        entry = self._entry()
        reg = AgentChannelIdentityRegistry([entry])
        reg.remove(channel="telegram", account_id="bot1")
        assert len(reg) == 0

    def test_all_entries(self) -> None:
        entries = [self._entry(), self._entry(channel="email", account_id="box@a.com")]
        reg = AgentChannelIdentityRegistry(entries)
        assert len(reg.all_entries()) == 2

    def test_is_owner_identity_hit(self) -> None:
        entry = self._entry()
        reg = AgentChannelIdentityRegistry([entry])
        import asyncio

        result = asyncio.run(
            reg.is_owner_identity(user_id="U1", channel="telegram", sender_id="owner_id")
        )
        assert result is True

    def test_is_owner_identity_miss(self) -> None:
        entry = self._entry()
        reg = AgentChannelIdentityRegistry([entry])
        import asyncio

        result = asyncio.run(
            reg.is_owner_identity(user_id="U1", channel="telegram", sender_id="stranger")
        )
        assert result is False

    def test_load_from_list_replaces(self) -> None:
        entry1 = self._entry(account_id="bot1")
        entry2 = self._entry(account_id="bot2")
        reg = AgentChannelIdentityRegistry([entry1])
        reg.load_from_list([entry2])
        assert len(reg) == 1
        import asyncio

        result = asyncio.run(reg.lookup(channel="telegram", account_id="bot2"))
        assert result is not None

    def test_deactivate_via_add(self) -> None:
        entry = self._entry()
        reg = AgentChannelIdentityRegistry([entry])
        disabled = entry.model_copy(update={"active": False})
        reg.add(disabled)
        import asyncio

        result = asyncio.run(reg.lookup(channel="telegram", account_id="bot1"))
        assert result is None


# ---------------------------------------------------------------------------
# FR-IN-002: AliasResolver.resolve_to_node
# ---------------------------------------------------------------------------


class TestAliasResolverCounterparty:
    """Unit tests for resolve_to_node (FR-IN-002)."""

    @pytest.mark.asyncio
    async def test_resolve_to_node_with_no_redis(self) -> None:
        from graphclaw.gateway.alias_resolver import AliasResolver

        resolver = AliasResolver(redis_client=None)
        result = await resolver.resolve_to_node("telegram", "cp_123", "USER-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_to_node_delegates_to_resolve(self, monkeypatch) -> None:
        from graphclaw.gateway.alias_resolver import AliasResolver

        resolver = AliasResolver(redis_client=None)

        async def fake_resolve(channel: str, sender_id: str) -> str | None:
            return "USER-found-123"

        monkeypatch.setattr(resolver, "resolve", fake_resolve)
        result = await resolver.resolve_to_node("email", "alice@example.com", "USER-001")
        assert result == "USER-found-123"


# ---------------------------------------------------------------------------
# AgentChannelIdentity model
# ---------------------------------------------------------------------------


class TestAgentChannelIdentityModel:
    """Basic model validation tests (FR-IN-003)."""

    def test_defaults(self) -> None:
        entry = AgentChannelIdentity(
            user_id="U1", agent_id="A1", channel="telegram", account_id="bot1"
        )
        assert entry.active is True
        assert entry.display_name == ""
        assert entry.credentials_ref == ""
        assert entry.owner_identities == []

    def test_custom_fields(self) -> None:
        entry = AgentChannelIdentity(
            user_id="U1",
            agent_id="A1",
            channel="email",
            account_id="inbox@corp.com",
            display_name="Corp Inbox",
            credentials_ref="gmail_token_u1",
            owner_identities=["admin@corp.com"],
            active=False,
        )
        assert entry.active is False
        assert "admin@corp.com" in entry.owner_identities

    def test_serialisation_roundtrip(self) -> None:
        entry = AgentChannelIdentity(
            user_id="U1", agent_id="A1", channel="telegram", account_id="bot1"
        )
        d = entry.model_dump()
        restored = AgentChannelIdentity(**d)
        assert restored == entry
