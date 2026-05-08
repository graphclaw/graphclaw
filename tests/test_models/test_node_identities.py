# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_node_identities — FR-GRAPH-001 acceptance tests.

Verifies:
  AC1: ChannelIdentities round-trips through Pydantic model serialisation.
  AC2: UserNode and ResourceNode have identities field with correct defaults.
  AC3: identities field accepts partial channel data.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.models.nodes import ChannelIdentities, ResourceNode, UserNode

_NOW = datetime.now(timezone.utc)


def _user(**kw) -> UserNode:
    return UserNode(
        id="USER-test",
        name="Alice",
        email="alice@test.com",
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


def _resource(**kw) -> ResourceNode:
    return ResourceNode(
        id="RES-test",
        name="Bob",
        resource_type="HUMAN",
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


class TestChannelIdentities:
    def test_default_empty(self) -> None:
        ci = ChannelIdentities()
        assert ci.emails == []
        assert ci.phones == []
        assert ci.telegram_id is None
        assert ci.telegram_username is None
        assert ci.whatsapp_id is None
        assert ci.slack_user_id is None

    def test_round_trip(self) -> None:
        ci = ChannelIdentities(
            emails=["alice@example.com"],
            phones=["+1234567890"],
            telegram_id="tg-12345",
            telegram_username="alice_tg",
            whatsapp_id="wa-12345",
            slack_user_id="U01234",
        )
        dumped = ci.model_dump(mode="json")
        restored = ChannelIdentities(**dumped)
        assert restored.emails == ["alice@example.com"]
        assert restored.telegram_id == "tg-12345"
        assert restored.slack_user_id == "U01234"

    def test_partial_fill(self) -> None:
        ci = ChannelIdentities(telegram_id="tg-99")
        assert ci.telegram_id == "tg-99"
        assert ci.emails == []


class TestUserNodeIdentities:
    def test_default_identities_empty(self) -> None:
        u = _user()
        assert u.identities.emails == []
        assert u.identities.telegram_id is None

    def test_set_identities(self) -> None:
        ci = ChannelIdentities(emails=["alice@corp.com"], telegram_id="tg-alice")
        u = _user(identities=ci)
        assert u.identities.emails == ["alice@corp.com"]
        assert u.identities.telegram_id == "tg-alice"

    def test_model_dump_includes_identities(self) -> None:
        ci = ChannelIdentities(slack_user_id="U9999")
        u = _user(identities=ci)
        d = u.model_dump(mode="json")
        assert d["identities"]["slack_user_id"] == "U9999"


class TestResourceNodeIdentities:
    def test_default_identities(self) -> None:
        r = _resource()
        assert r.identities.emails == []
        assert r.identities.whatsapp_id is None

    def test_set_identities(self) -> None:
        ci = ChannelIdentities(phones=["+441234"], whatsapp_id="wa-bob")
        r = _resource(identities=ci)
        assert r.identities.phones == ["+441234"]
        assert r.identities.whatsapp_id == "wa-bob"
