# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_mcp.test_mcp_registry — Unit tests for MCPRegistry.

Description
-----------
Tests ``MCPRegistry`` CRUD operations against a ``FakeStorageClient``.
All async calls use the in-memory storage fake so no real MinIO is needed.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- graphclaw.mcp.registry: MCPRegistry.
- graphclaw.models.enums: MCPTransport, TrustTier.
- graphclaw.models.nodes: MCPServerNode.
- tests.test_api.conftest: FakeStorageClient.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.mcp.registry import MCPRegistry
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode
from tests.test_api.conftest import FakeStorageClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER = "USER-alice"


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


def make_registry() -> tuple[MCPRegistry, FakeStorageClient]:
    storage = FakeStorageClient()
    return MCPRegistry(storage_client=storage), storage


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestMCPRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_writes_json_to_storage(self):
        registry, storage = make_registry()
        node = make_server_node()

        result = await registry.register(_USER, node)

        assert result is node
        assert await storage.exists(f"{_USER}/mcp/servers/{node.id}.json")

    @pytest.mark.asyncio
    async def test_register_returns_readable_node(self):
        registry, _ = make_registry()
        node = make_server_node()

        await registry.register(_USER, node)
        retrieved = await registry.get(_USER, node.id)

        assert retrieved is not None
        assert retrieved.id == node.id
        assert retrieved.name == node.name


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestMCPRegistryGet:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_found(self):
        registry, _ = make_registry()
        result = await registry.get(_USER, "MCP-MISSING1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_node_after_register(self):
        registry, _ = make_registry()
        node = make_server_node()
        await registry.register(_USER, node)

        result = await registry.get(_USER, node.id)

        assert result is not None
        assert result.trust_tier == node.trust_tier


# ---------------------------------------------------------------------------
# list_for_user
# ---------------------------------------------------------------------------


class TestMCPRegistryListForUser:
    @pytest.mark.asyncio
    async def test_list_returns_enabled_nodes_only_by_default(self):
        registry, _ = make_registry()
        enabled = make_server_node("MCP-AAAABBBB", enabled=True)
        disabled = make_server_node("MCP-CCCCDDDD", enabled=False)

        await registry.register(_USER, enabled)
        await registry.register(_USER, disabled)

        results = await registry.list_for_user(_USER, enabled_only=True)

        assert len(results) == 1
        assert results[0].id == "MCP-AAAABBBB"

    @pytest.mark.asyncio
    async def test_list_returns_all_when_enabled_only_false(self):
        registry, _ = make_registry()
        enabled = make_server_node("MCP-AAAABBBB", enabled=True)
        disabled = make_server_node("MCP-CCCCDDDD", enabled=False)

        await registry.register(_USER, enabled)
        await registry.register(_USER, disabled)

        results = await registry.list_for_user(_USER, enabled_only=False)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_returns_empty_when_no_servers(self):
        registry, _ = make_registry()
        results = await registry.list_for_user("USER-nobody")
        assert results == []


# ---------------------------------------------------------------------------
# update_trust
# ---------------------------------------------------------------------------


class TestMCPRegistryUpdateTrust:
    @pytest.mark.asyncio
    async def test_update_trust_changes_tier(self):
        registry, _ = make_registry()
        node = make_server_node(trust_tier=TrustTier.GATED)
        await registry.register(_USER, node)

        result = await registry.update_trust(_USER, node.id, TrustTier.AUTO)

        assert result.trust_tier == TrustTier.AUTO
        persisted = await registry.get(_USER, node.id)
        assert persisted is not None
        assert persisted.trust_tier == TrustTier.AUTO

    @pytest.mark.asyncio
    async def test_blocked_to_auto_raises_value_error(self):
        registry, _ = make_registry()
        node = make_server_node(trust_tier=TrustTier.BLOCKED)
        await registry.register(_USER, node)

        with pytest.raises(ValueError, match="BLOCKED"):
            await registry.update_trust(_USER, node.id, TrustTier.AUTO)

    @pytest.mark.asyncio
    async def test_blocked_to_gated_succeeds(self):
        registry, _ = make_registry()
        node = make_server_node(trust_tier=TrustTier.BLOCKED)
        await registry.register(_USER, node)

        result = await registry.update_trust(_USER, node.id, TrustTier.GATED)

        assert result.trust_tier == TrustTier.GATED

    @pytest.mark.asyncio
    async def test_update_trust_raises_if_not_found(self):
        registry, _ = make_registry()

        with pytest.raises(ValueError, match="not found"):
            await registry.update_trust(_USER, "MCP-MISSING1", TrustTier.AUTO)


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


class TestMCPRegistryEnableDisable:
    @pytest.mark.asyncio
    async def test_enable_sets_enabled_true(self):
        registry, _ = make_registry()
        node = make_server_node(enabled=False)
        await registry.register(_USER, node)

        await registry.enable(_USER, node.id)

        result = await registry.get(_USER, node.id)
        assert result is not None
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_disable_sets_enabled_false(self):
        registry, _ = make_registry()
        node = make_server_node(enabled=True)
        await registry.register(_USER, node)

        await registry.disable(_USER, node.id)

        result = await registry.get(_USER, node.id)
        assert result is not None
        assert result.enabled is False


# ---------------------------------------------------------------------------
# deregister
# ---------------------------------------------------------------------------


class TestMCPRegistryDeregister:
    @pytest.mark.asyncio
    async def test_deregister_removes_node(self):
        registry, _ = make_registry()
        node = make_server_node()
        await registry.register(_USER, node)

        await registry.deregister(_USER, node.id)

        result = await registry.get(_USER, node.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_deregister_noop_when_not_found(self):
        registry, _ = make_registry()
        # Should not raise
        await registry.deregister(_USER, "MCP-MISSING1")


# ---------------------------------------------------------------------------
# find_by_scope
# ---------------------------------------------------------------------------


class TestMCPRegistryFindByScope:
    @pytest.mark.asyncio
    async def test_find_by_scope_returns_matching_nodes(self):
        registry, _ = make_registry()
        cal_node = make_server_node("MCP-AAAABBBB", scope=["calendar:read", "calendar:write"])
        gh_node = make_server_node("MCP-CCCCDDDD", scope=["github:read"])

        await registry.register(_USER, cal_node)
        await registry.register(_USER, gh_node)

        results = await registry.find_by_scope(_USER, "calendar:read")

        assert len(results) == 1
        assert results[0].id == "MCP-AAAABBBB"

    @pytest.mark.asyncio
    async def test_find_by_scope_returns_empty_when_no_match(self):
        registry, _ = make_registry()
        gh_node = make_server_node("MCP-CCCCDDDD", scope=["github:read"])
        await registry.register(_USER, gh_node)

        results = await registry.find_by_scope(_USER, "calendar:read")
        assert results == []
