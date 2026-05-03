"""tests.test_db.test_tombstone_resolver — FR-DEL-003 acceptance tests.

Verifies resolve_canonical():
  AC1: A→B→C chain resolves to C in one call.
  AC2: Cycle A→B→A raises TombstoneCycle.
  AC3: Default reads exclude archived nodes (include_archived=False by default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.db.age.redirects import MaxHopsExceeded, TombstoneCycle, resolve_canonical


def _mock_store(nodes: dict[str, dict | None]) -> object:
    """Build a minimal mock GraphStore that returns nodes from a dict."""
    store = MagicMock()

    async def _get_node(node_id: str, include_archived: bool = False) -> dict | None:
        raw = nodes.get(node_id)
        if raw is None:
            return None
        if not include_archived and raw.get("archived_at"):
            return None
        return raw

    async def _list_nodes(label: str, filters: dict | None = None) -> list[dict]:
        """Return TombstoneNodes matching the archived_node_id filter."""
        results = []
        for node in nodes.values():
            if node and node.get("node_type") == "TombstoneNode":
                if filters and node.get("archived_node_id") == filters.get("archived_node_id"):
                    results.append(node)
        return results

    store.get_node = _get_node
    store.list_nodes = _list_nodes
    return store


class TestResolveCanonicalChain:
    """AC1: Multi-hop chain resolves to final live node."""

    async def test_single_live_node(self) -> None:
        """A live node resolves to itself."""
        nodes = {
            "A": {"id": "A", "title": "live A"},
        }
        store = _mock_store(nodes)
        result = await resolve_canonical("A", store)
        assert result["id"] == "A"

    async def test_direct_redirect(self) -> None:
        """A→B: A is archived with redirect to B; resolves to B."""
        nodes = {
            "A": {
                "id": "A",
                "archived_at": "2026-01-01",
                "link_status": "redirected",
                "node_type": "TaskNode",
            },
            "B": {"id": "B", "title": "live B"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": "B",
            },
        }
        store = _mock_store(nodes)
        result = await resolve_canonical("A", store, max_hops=5)
        assert result is not None
        assert result["id"] == "B"

    async def test_three_hop_chain(self) -> None:
        """AC1: A→B→C chain resolves to C in one resolve_canonical call."""
        nodes = {
            "A": {"id": "A", "archived_at": "2026-01-01", "link_status": "redirected"},
            "B": {"id": "B", "archived_at": "2026-01-02", "link_status": "redirected"},
            "C": {"id": "C", "title": "live C"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": "B",
            },
            "tomb_B": {
                "id": "tomb_B",
                "node_type": "TombstoneNode",
                "archived_node_id": "B",
                "redirect_to": "C",
            },
        }
        store = _mock_store(nodes)
        result = await resolve_canonical("A", store, max_hops=5)
        assert result is not None
        assert result["id"] == "C"

    async def test_redirect_to_none(self) -> None:
        """Tombstone with redirect_to=None returns None (node deleted, no replacement)."""
        nodes = {
            "A": {"id": "A", "archived_at": "2026-01-01", "link_status": "redirected"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": None,
            },
        }
        store = _mock_store(nodes)
        result = await resolve_canonical("A", store, max_hops=5)
        assert result is None

    async def test_missing_node_returns_none(self) -> None:
        """Resolving a non-existent node returns None."""
        store = _mock_store({})
        result = await resolve_canonical("DOES_NOT_EXIST", store)
        assert result is None


class TestResolveCanonicalCycle:
    """AC2: Cycle detection raises TombstoneCycle."""

    async def test_self_cycle(self) -> None:
        """A→A tombstone raises TombstoneCycle."""
        nodes = {
            "A": {"id": "A", "archived_at": "2026-01-01", "link_status": "redirected"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": "A",
            },
        }
        store = _mock_store(nodes)
        with pytest.raises(TombstoneCycle):
            await resolve_canonical("A", store)

    async def test_two_node_cycle(self) -> None:
        """A→B→A cycle raises TombstoneCycle."""
        nodes = {
            "A": {"id": "A", "archived_at": "2026-01-01", "link_status": "redirected"},
            "B": {"id": "B", "archived_at": "2026-01-02", "link_status": "redirected"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": "B",
            },
            "tomb_B": {
                "id": "tomb_B",
                "node_type": "TombstoneNode",
                "archived_node_id": "B",
                "redirect_to": "A",
            },
        }
        store = _mock_store(nodes)
        with pytest.raises(TombstoneCycle):
            await resolve_canonical("A", store)


class TestResolveCanonicalMaxHops:
    """Max-hop limit prevents infinite chains."""

    async def test_max_hops_exceeded(self) -> None:
        """Chain longer than max_hops raises MaxHopsExceeded."""
        # Build chain A→B→C→D→E (5 nodes) with max_hops=2
        nodes = {}
        prev = None
        for letter in ["E", "D", "C", "B", "A"]:
            nodes[letter] = {
                "id": letter,
                "archived_at": "2026-01-01",
                "link_status": "redirected",
            }
            if prev is not None:
                nodes[f"tomb_{letter}"] = {
                    "id": f"tomb_{letter}",
                    "node_type": "TombstoneNode",
                    "archived_node_id": letter,
                    "redirect_to": prev,
                }
            prev = letter
        # Make E a live node (final target).
        nodes["E"] = {"id": "E", "title": "live E"}

        store = _mock_store(nodes)
        with pytest.raises(MaxHopsExceeded):
            await resolve_canonical("A", store, max_hops=2)

    async def test_at_max_hops_succeeds(self) -> None:
        """Chain at exactly max_hops resolves successfully."""
        nodes = {
            "A": {"id": "A", "archived_at": "2026-01-01", "link_status": "redirected"},
            "B": {"id": "B", "title": "live B"},
            "tomb_A": {
                "id": "tomb_A",
                "node_type": "TombstoneNode",
                "archived_node_id": "A",
                "redirect_to": "B",
            },
        }
        store = _mock_store(nodes)
        result = await resolve_canonical("A", store, max_hops=1)
        assert result is not None
        assert result["id"] == "B"


class TestArchivedNodeExclusion:
    """AC3: Default reads exclude archived nodes."""

    async def test_get_node_excludes_archived_by_default(self) -> None:
        """AgeGraphStore.get_node(include_archived=False) returns None for archived nodes."""
        from unittest.mock import patch

        from graphclaw.db.age.repository import AgeGraphStore

        store = AgeGraphStore(pool=MagicMock(), principal_name="agent_principal")

        # Mock fetchone to return a node that has archived_at set.
        archived_row = MagicMock()

        mock_conn = AsyncMock()
        mock_result = AsyncMock()

        async def _fake_fetchone():
            return (archived_row,)

        mock_result.fetchone = _fake_fetchone
        mock_conn.execute = AsyncMock(return_value=mock_result)

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_MockCtx()):
            with patch(
                "graphclaw.db.age.repository._extract_properties",
                return_value={"id": "A", "archived_at": "2026-01-01", "title": "archived"},
            ):
                result = await store.get_node("A")  # include_archived defaults to False

        assert result is None, "Archived node should be excluded by default"

    async def test_get_node_includes_archived_when_requested(self) -> None:
        """AgeGraphStore.get_node(include_archived=True) returns archived nodes."""
        from unittest.mock import patch

        from graphclaw.db.age.repository import AgeGraphStore

        store = AgeGraphStore(pool=MagicMock(), principal_name="agent_principal")

        archived_row = MagicMock()
        mock_conn = AsyncMock()
        mock_result = AsyncMock()

        async def _fake_fetchone():
            return (archived_row,)

        mock_result.fetchone = _fake_fetchone
        mock_conn.execute = AsyncMock(return_value=mock_result)

        class _MockCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        with patch("graphclaw.db.age.repository.get_connection", return_value=_MockCtx()):
            with patch(
                "graphclaw.db.age.repository._extract_properties",
                return_value={"id": "A", "archived_at": "2026-01-01", "title": "archived"},
            ):
                result = await store.get_node("A", include_archived=True)

        assert result is not None
        assert result["id"] == "A"


class TestMigration0010:
    """Migration 0010 creates TombstoneNode vlabel."""

    def test_migration_0010_exists(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        versions = [m.version for m in MIGRATIONS]
        assert "0010" in versions

    def test_migration_0010_creates_tombstone_label(self) -> None:
        from graphclaw.migrations.catalogue import MIGRATIONS

        m = next(m for m in MIGRATIONS if m.version == "0010")
        assert "TombstoneNode" in m.sql_up
