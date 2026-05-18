# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_node_aliases — FR-GRAPH-002 acceptance tests.

Verifies:
  AC1: AliasEntry has provenance fields (value, added_at, added_by, source).
  AC2: Aliases list is empty by default on UserNode + ResourceNode.
  AC3: Multiple aliases can be stored with distinct provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.models.nodes import AliasEntry, ResourceNode, UserNode

_NOW = datetime.now(timezone.utc)


def _user(**kw) -> UserNode:
    return UserNode(
        id="USER-alice",
        name="Alice",
        email="alice@test.com",
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


def _resource(**kw) -> ResourceNode:
    return ResourceNode(
        id="RES-bob",
        name="Bob",
        resource_type="HUMAN",
        created_at=_NOW,
        updated_at=_NOW,
        **kw,
    )


class TestAliasEntry:
    def test_required_fields(self) -> None:
        alias = AliasEntry(value="Bobby", added_by="USER-admin", source="manual")
        assert alias.value == "Bobby"
        assert alias.added_by == "USER-admin"
        assert alias.source == "manual"
        assert alias.added_at is not None

    def test_round_trip(self) -> None:
        alias = AliasEntry(
            value="Rob",
            added_by="SYSTEM",
            source="inbound_match",
            added_at=_NOW,
        )
        d = alias.model_dump(mode="json")
        restored = AliasEntry(**d)
        assert restored.value == "Rob"
        assert restored.source == "inbound_match"


class TestUserNodeAliases:
    def test_default_empty(self) -> None:
        u = _user()
        assert u.aliases == []

    def test_add_alias(self) -> None:
        aliases = [
            AliasEntry(value="Alice Smith", added_by="SYSTEM", source="onboarding"),
        ]
        u = _user(aliases=aliases)
        assert len(u.aliases) == 1
        assert u.aliases[0].value == "Alice Smith"

    def test_multiple_aliases_with_provenance(self) -> None:
        aliases = [
            AliasEntry(value="Ally", added_by="USER-admin", source="manual"),
            AliasEntry(value="Al", added_by="SYSTEM", source="alias_drift"),
        ]
        u = _user(aliases=aliases)
        assert len(u.aliases) == 2
        sources = {a.source for a in u.aliases}
        assert sources == {"manual", "alias_drift"}


class TestResourceNodeAliases:
    def test_default_empty(self) -> None:
        r = _resource()
        assert r.aliases == []

    def test_add_alias(self) -> None:
        aliases = [AliasEntry(value="Robert", added_by="USER-1", source="manual")]
        r = _resource(aliases=aliases)
        assert r.aliases[0].value == "Robert"
