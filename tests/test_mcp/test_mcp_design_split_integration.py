"""Integration tests for Section 10 MCP design split.

Verifies that:
1. MCP server configs are persisted in object storage as JSON.
2. MCP approval tasks are read from the graph store (AGE).
3. Legacy MCP graph labels are absent after migration cleanup.

Run with::

    pytest tests/test_mcp/test_mcp_design_split_integration.py -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.db.connection import create_pool
from graphclaw.infra.storage import S3StorageClient, StoragePaths
from graphclaw.mcp.approval import GatedApprovalService
from graphclaw.mcp.registry import MCPRegistry
from graphclaw.models.base import utcnow
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)

BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")
AWS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "graphclaw")
AWS_SECRET = os.getenv("AWS_SECRET_ACCESS_KEY", "graphclaw_dev")


@pytest_asyncio.fixture(scope="module")
async def pool():
    p = await create_pool(TEST_DSN)
    yield p
    await p.close()


@pytest_asyncio.fixture(scope="module")
async def graph_store(pool):
    return AgeGraphStore(pool)


@pytest_asyncio.fixture(scope="module")
async def storage():
    return S3StorageClient(
        bucket=BUCKET,
        endpoint_url=ENDPOINT,
        region=REGION,
        aws_access_key_id=AWS_KEY,
        aws_secret_access_key=AWS_SECRET,
    )


@pytest_asyncio.fixture
async def mcp_registry(storage):
    return MCPRegistry(storage_client=storage)


class TestMCPDesignSplitIntegration:
    @pytest.mark.asyncio
    async def test_registry_persists_server_json_in_object_storage(self, mcp_registry, storage):
        user_id = f"USER-mcp-int-{uuid.uuid4().hex[:8]}"
        server_id = f"MCP-int-{uuid.uuid4().hex[:8]}"
        now = utcnow()

        node = MCPServerNode(
            id=server_id,
            name="Integration MCP Server",
            transport=MCPTransport.HTTP,
            endpoint_url="https://example.com/mcp",
            trust_tier=TrustTier.GATED,
            scope=["calendar:read"],
            enabled=True,
            created_at=now,
            updated_at=now,
            version=0,
        )

        await mcp_registry.register(user_id, node)
        path = StoragePaths.mcp_server(user_id, server_id)

        try:
            assert await storage.exists(path)
            loaded = await mcp_registry.get(user_id, server_id)
            assert loaded is not None
            assert loaded.id == server_id
            assert loaded.name == "Integration MCP Server"
        finally:
            await mcp_registry.deregister(user_id, server_id)

    @pytest.mark.asyncio
    async def test_mcp_approvals_are_sourced_from_graph_store(self, graph_store):
        user_id = f"USER-mcp-approval-{uuid.uuid4().hex[:8]}"
        service = GatedApprovalService(graph_store=graph_store)

        task_id = await service.request_approval(
            user_id=user_id,
            tool_name="calendar.create_event",
            server_name="Calendar MCP",
            arguments={"title": "Design Review", "date": "2026-04-21"},
        )

        try:
            pending = await service.get_pending_approvals(user_id)
            pending_ids = {t.get("id") for t in pending}
            assert task_id in pending_ids
        finally:
            await graph_store.delete_node(task_id)

    @pytest.mark.asyncio
    async def test_legacy_mcp_graph_labels_are_absent(self, pool):
        async with pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT name
                FROM ag_catalog.ag_label
                WHERE graph = (
                    SELECT graphid
                    FROM ag_catalog.ag_graph
                    WHERE name = 'graphclaw'
                )
                AND name IN ('MCPServerNode', 'GRANTS_ACCESS_TO_MCP')
                ORDER BY name;
                """
            )
            rows = await result.fetchall()

        assert rows == []
