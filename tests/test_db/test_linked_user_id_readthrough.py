"""tests.test_db.test_linked_user_id — FR-GRAPH-003 acceptance tests (unit).

Verifies get_resource_with_linked_view():
  AC1: Resource with linked_user_id returns merged view with user's prefs + identities.
  AC2: Resource without linked_user_id returns itself unmodified.
  AC3: Resource with linked_user_id that resolves to None returns original resource.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from graphclaw.models.enums import LinkStatus


def _mock_store(node_map: dict) -> object:
    """Build minimal mock GraphStore for linked-view tests."""
    store = MagicMock()

    async def _get_node(node_id, include_archived=False, caller_context=None):
        return node_map.get(node_id)

    store.get_node = AsyncMock(side_effect=_get_node)
    return store


class TestGetResourceWithLinkedView:
    async def test_no_linked_user_returns_resource(self) -> None:
        """Resource with no linked_user_id returns itself."""
        from graphclaw.db.age.repository import AgeGraphStore

        resource = {
            "id": "RES-bob",
            "name": "Bob",
            "linked_user_id": None,
            "link_status": LinkStatus.ACTIVE.value,
        }
        store = _mock_store({"RES-bob": resource})
        store.__class__ = AgeGraphStore

        # Directly call the method logic since we're unit testing it.
        result = await AgeGraphStore.get_resource_with_linked_view(store, "RES-bob")
        assert result["id"] == "RES-bob"
        assert "preferences" not in result or result.get("linked_user_id") is None

    async def test_linked_user_merges_preferences(self) -> None:
        """Resource with linked_user_id gets user's preferences overlaid."""
        from graphclaw.db.age.repository import AgeGraphStore

        resource = {
            "id": "RES-bob",
            "name": "Bob (shadow)",
            "linked_user_id": "USER-bob",
            "link_status": LinkStatus.ACTIVE.value,
            "owner_notes": "my notes",
        }
        user = {
            "id": "USER-bob",
            "name": "Bob",
            "preferences": {"preferred_channel": "telegram"},
            "identities": {"telegram_id": "tg-bob"},
        }
        store = _mock_store({"RES-bob": resource, "USER-bob": user})
        store.__class__ = AgeGraphStore

        result = await AgeGraphStore.get_resource_with_linked_view(store, "RES-bob")
        assert result["preferences"]["preferred_channel"] == "telegram"
        assert result["identities"]["telegram_id"] == "tg-bob"
        # Owner-specific field preserved.
        assert result["owner_notes"] == "my notes"

    async def test_linked_user_not_found_returns_resource(self) -> None:
        """Resource with linked_user_id pointing to missing user returns original."""
        from graphclaw.db.age.repository import AgeGraphStore

        resource = {
            "id": "RES-orphan",
            "name": "Orphan Shadow",
            "linked_user_id": "USER-purged",
            "link_status": LinkStatus.DETACHED_USER_PURGED.value,
        }
        store = _mock_store({"RES-orphan": resource, "USER-purged": None})
        store.__class__ = AgeGraphStore

        result = await AgeGraphStore.get_resource_with_linked_view(store, "RES-orphan")
        assert result["id"] == "RES-orphan"

    async def test_resource_not_found_returns_none(self) -> None:
        """Missing resource returns None."""
        from graphclaw.db.age.repository import AgeGraphStore

        store = _mock_store({})
        store.__class__ = AgeGraphStore

        result = await AgeGraphStore.get_resource_with_linked_view(store, "RES-missing")
        assert result is None
