"""tests.test_mcp.test_mcp_registry — Unit tests for MCPRegistry.

Description
-----------
Tests ``MCPRegistry`` CRUD operations using a mock ``GraphStore``.
All async calls are mocked with ``AsyncMock`` so the tests run without a
database.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.mcp.registry: MCPRegistry.
- graphclaw.models.enums: EdgeType, MCPTransport, TrustTier.
- graphclaw.models.nodes: MCPServerNode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from graphclaw.mcp.registry import MCPRegistry
from graphclaw.models.enums import EdgeType, MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_server_node(
    server_id: str = "MCP-AAAABBBB",
    name: str = "Test Server",
    trust_tier: TrustTier = TrustTier.GATED,
    enabled: bool = True,
    scope: list[str] | None = None,
) -> MCPServerNode:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return MCPServerNode(
        id=server_id,
        name=name,
        transport=MCPTransport.HTTP,
        endpoint_url="https://example.com/mcp",
        trust_tier=trust_tier,
        scope=scope or [],
        enabled=enabled,
        registered_at=now,
        created_at=now,
        updated_at=now,
        version=0,
    )


def make_store() -> AsyncMock:
    store = AsyncMock()
    store.create_node = AsyncMock(return_value={})
    store.create_edge = AsyncMock(return_value={})
    store.get_node = AsyncMock(return_value=None)
    store.update_node = AsyncMock(return_value={})
    store.delete_node = AsyncMock(return_value=None)
    store.list_nodes = AsyncMock(return_value=[])
    store.get_edges = AsyncMock(return_value=[])
    return store


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestMCPRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_creates_node_and_edge(self):
        store = make_store()
        registry = MCPRegistry(store)
        node = make_server_node()

        result = await registry.register("USER-alice", node)

        store.create_node.assert_awaited_once_with(node)
        store.create_edge.assert_awaited_once()
        edge_call = store.create_edge.call_args
        assert (
            edge_call.kwargs.get("source_id") == "USER-alice" or edge_call.args[0] == "USER-alice"
        )
        assert result is node

    @pytest.mark.asyncio
    async def test_register_uses_grants_access_to_mcp_edge(self):
        store = make_store()
        registry = MCPRegistry(store)
        node = make_server_node()

        await registry.register("USER-bob", node)

        edge_call_kwargs = store.create_edge.call_args.kwargs
        assert edge_call_kwargs["edge_type"] == EdgeType.GRANTS_ACCESS_TO_MCP


# ---------------------------------------------------------------------------
# list_for_user
# ---------------------------------------------------------------------------


class TestMCPRegistryListForUser:
    @pytest.mark.asyncio
    async def test_list_returns_enabled_nodes_only_by_default(self):
        enabled_node = make_server_node("MCP-AAAABBBB", enabled=True)
        disabled_node = make_server_node("MCP-CCCCDDDD", enabled=False)

        store = make_store()
        # Two edges from the user
        store.get_edges = AsyncMock(
            return_value=[
                {"target_id": "MCP-AAAABBBB"},
                {"target_id": "MCP-CCCCDDDD"},
            ]
        )

        def get_node_side_effect(node_id):
            if node_id == "MCP-AAAABBBB":
                return enabled_node.model_dump()
            if node_id == "MCP-CCCCDDDD":
                return disabled_node.model_dump()
            return None

        store.get_node = AsyncMock(side_effect=get_node_side_effect)

        registry = MCPRegistry(store)
        results = await registry.list_for_user("USER-alice", enabled_only=True)

        assert len(results) == 1
        assert results[0].id == "MCP-AAAABBBB"

    @pytest.mark.asyncio
    async def test_list_returns_all_when_enabled_only_false(self):
        enabled_node = make_server_node("MCP-AAAABBBB", enabled=True)
        disabled_node = make_server_node("MCP-CCCCDDDD", enabled=False)

        store = make_store()
        store.get_edges = AsyncMock(
            return_value=[
                {"target_id": "MCP-AAAABBBB"},
                {"target_id": "MCP-CCCCDDDD"},
            ]
        )

        def get_node_side_effect(node_id):
            if node_id == "MCP-AAAABBBB":
                return enabled_node.model_dump()
            if node_id == "MCP-CCCCDDDD":
                return disabled_node.model_dump()
            return None

        store.get_node = AsyncMock(side_effect=get_node_side_effect)

        registry = MCPRegistry(store)
        results = await registry.list_for_user("USER-alice", enabled_only=False)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_returns_empty_when_no_edges(self):
        store = make_store()
        store.get_edges = AsyncMock(return_value=[])

        registry = MCPRegistry(store)
        results = await registry.list_for_user("USER-nobody")
        assert results == []


# ---------------------------------------------------------------------------
# update_trust
# ---------------------------------------------------------------------------


class TestMCPRegistryUpdateTrust:
    @pytest.mark.asyncio
    async def test_update_trust_changes_tier(self):
        node = make_server_node(trust_tier=TrustTier.GATED)
        updated_node = make_server_node(trust_tier=TrustTier.AUTO)

        store = make_store()
        store.get_node = AsyncMock(return_value=node.model_dump())
        store.update_node = AsyncMock(return_value=updated_node.model_dump())

        registry = MCPRegistry(store)
        result = await registry.update_trust("MCP-AAAABBBB", TrustTier.AUTO)

        store.update_node.assert_awaited_once()
        assert result.trust_tier == TrustTier.AUTO

    @pytest.mark.asyncio
    async def test_blocked_to_auto_raises_value_error(self):
        node = make_server_node(trust_tier=TrustTier.BLOCKED)

        store = make_store()
        store.get_node = AsyncMock(return_value=node.model_dump())

        registry = MCPRegistry(store)
        with pytest.raises(ValueError, match="BLOCKED"):
            await registry.update_trust("MCP-AAAABBBB", TrustTier.AUTO)

    @pytest.mark.asyncio
    async def test_blocked_to_gated_succeeds(self):
        node = make_server_node(trust_tier=TrustTier.BLOCKED)
        updated_node = make_server_node(trust_tier=TrustTier.GATED)

        store = make_store()
        store.get_node = AsyncMock(return_value=node.model_dump())
        store.update_node = AsyncMock(return_value=updated_node.model_dump())

        registry = MCPRegistry(store)
        result = await registry.update_trust("MCP-AAAABBBB", TrustTier.GATED)
        assert result.trust_tier == TrustTier.GATED

    @pytest.mark.asyncio
    async def test_update_trust_raises_if_not_found(self):
        store = make_store()
        store.get_node = AsyncMock(return_value=None)

        registry = MCPRegistry(store)
        with pytest.raises(ValueError, match="not found"):
            await registry.update_trust("MCP-MISSING1", TrustTier.AUTO)


# ---------------------------------------------------------------------------
# find_by_scope
# ---------------------------------------------------------------------------


class TestMCPRegistryFindByScope:
    @pytest.mark.asyncio
    async def test_find_by_scope_returns_matching_nodes(self):
        cal_node = make_server_node("MCP-AAAABBBB", scope=["calendar:read", "calendar:write"])
        gh_node = make_server_node("MCP-CCCCDDDD", scope=["github:read"])

        store = make_store()
        store.get_edges = AsyncMock(
            return_value=[
                {"target_id": "MCP-AAAABBBB"},
                {"target_id": "MCP-CCCCDDDD"},
            ]
        )

        def get_node_side_effect(node_id):
            if node_id == "MCP-AAAABBBB":
                return cal_node.model_dump()
            if node_id == "MCP-CCCCDDDD":
                return gh_node.model_dump()
            return None

        store.get_node = AsyncMock(side_effect=get_node_side_effect)

        registry = MCPRegistry(store)
        results = await registry.find_by_scope("USER-alice", "calendar:read")

        assert len(results) == 1
        assert results[0].id == "MCP-AAAABBBB"

    @pytest.mark.asyncio
    async def test_find_by_scope_returns_empty_when_no_match(self):
        gh_node = make_server_node("MCP-CCCCDDDD", scope=["github:read"])

        store = make_store()
        store.get_edges = AsyncMock(return_value=[{"target_id": "MCP-CCCCDDDD"}])
        store.get_node = AsyncMock(return_value=gh_node.model_dump())

        registry = MCPRegistry(store)
        results = await registry.find_by_scope("USER-alice", "calendar:read")
        assert results == []
