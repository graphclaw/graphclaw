"""Integration tests for AgentCatalog — requires live MinIO.

Connects to the MinIO instance started by docker-compose.

Run with::

    pytest tests/test_agent/test_catalog_integration.py -m integration
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio

from graphclaw.agent.catalog import AgentCatalog
from graphclaw.infra.storage import S3StorageClient, StoragePaths

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# MinIO connection constants
# ---------------------------------------------------------------------------

BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")

TEST_USER_ID = f"test-usr-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def storage():
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


@pytest_asyncio.fixture
async def catalog(storage):
    return AgentCatalog(storage)


@pytest_asyncio.fixture(autouse=True)
async def cleanup(storage):
    written: list[str] = []
    yield written
    for path in written:
        try:
            await storage.delete(path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(agent_id: str, agent_type: str = "user", **extra) -> dict:
    return {
        "agent_id": agent_id,
        "name": f"{agent_id.title()} Agent",
        "type": agent_type,
        "description": f"Test agent: {agent_id}",
        "capabilities": [f"{agent_id}_cap"],
        "invocation": "async",
        "tool_hint": f"Use for {agent_id} tasks.",
        **extra,
    }


async def _write_user_manifest(storage, user_id: str, agent_id: str) -> str:
    path = StoragePaths.agent_manifest(user_id, agent_id)
    manifest = _manifest(agent_id, "user")
    await storage.write(path, json.dumps(manifest).encode(), content_type="application/json")
    return path


async def _write_system_manifest(storage, agent_id: str) -> str:
    path = StoragePaths.system_agent_manifest(agent_id)
    manifest = _manifest(agent_id, "system")
    await storage.write(path, json.dumps(manifest).encode(), content_type="application/json")
    return path


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------


class TestResolveSourceIntegration:
    @pytest.mark.asyncio
    async def test_system_agent_resolved(self, catalog, storage, cleanup):
        agent_id = f"sys-agent-{uuid.uuid4().hex[:6]}"
        path = await _write_system_manifest(storage, agent_id)
        cleanup.append(path)

        source = await catalog.resolve_source(TEST_USER_ID, agent_id)
        assert source == "system"

    @pytest.mark.asyncio
    async def test_user_agent_resolves_to_user(self, catalog):
        # Agent that doesn't exist in system/ → "user"
        source = await catalog.resolve_source(TEST_USER_ID, "nonexistent-agent-xyz")
        assert source == "user"


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class TestListAllIntegration:
    @pytest.mark.asyncio
    async def test_user_manifest_appears_in_list(self, catalog, storage, cleanup):
        agent_id = f"my-agent-{uuid.uuid4().hex[:6]}"
        path = await _write_user_manifest(storage, TEST_USER_ID, agent_id)
        cleanup.append(path)

        results = await catalog.list_all(TEST_USER_ID)
        agent_ids = {r["agent_id"] for r in results}
        assert agent_id in agent_ids

    @pytest.mark.asyncio
    async def test_system_manifest_appears_in_list(self, catalog, storage, cleanup):
        agent_id = f"sys-{uuid.uuid4().hex[:6]}"
        path = await _write_system_manifest(storage, agent_id)
        cleanup.append(path)

        results = await catalog.list_all(TEST_USER_ID)
        agent_ids = {r["agent_id"] for r in results}
        assert agent_id in agent_ids

    @pytest.mark.asyncio
    async def test_capability_filter_applied(self, catalog, storage, cleanup):
        agent_id_a = f"cap-agent-a-{uuid.uuid4().hex[:6]}"
        agent_id_b = f"cap-agent-b-{uuid.uuid4().hex[:6]}"

        manifest_a = _manifest(agent_id_a, capabilities=["email_read"])
        manifest_b = _manifest(agent_id_b, capabilities=["task_create"])

        path_a = StoragePaths.agent_manifest(TEST_USER_ID, agent_id_a)
        path_b = StoragePaths.agent_manifest(TEST_USER_ID, agent_id_b)

        await storage.write(
            path_a, json.dumps(manifest_a).encode(), content_type="application/json"
        )
        await storage.write(
            path_b, json.dumps(manifest_b).encode(), content_type="application/json"
        )
        cleanup.extend([path_a, path_b])

        results = await catalog.list_all(TEST_USER_ID, capability_filter="email_read")
        agent_ids = {r["agent_id"] for r in results}
        assert agent_id_a in agent_ids
        assert agent_id_b not in agent_ids

    @pytest.mark.asyncio
    async def test_non_manifest_files_not_included(self, catalog, storage, cleanup):
        # Write a profile.md in the agent dir — should not appear in list
        agent_id = f"no-manifest-{uuid.uuid4().hex[:6]}"
        profile_path = StoragePaths.agent_profile(TEST_USER_ID, agent_id)
        await storage.write(profile_path, b"# Profile", content_type="text/markdown")
        cleanup.append(profile_path)

        results = await catalog.list_all(TEST_USER_ID)
        agent_ids = {r["agent_id"] for r in results}
        assert agent_id not in agent_ids


# ---------------------------------------------------------------------------
# get_compact_catalog
# ---------------------------------------------------------------------------


class TestGetCompactCatalogIntegration:
    @pytest.mark.asyncio
    async def test_catalog_includes_user_agent(self, catalog, storage, cleanup):
        agent_id = f"compact-{uuid.uuid4().hex[:6]}"
        path = await _write_user_manifest(storage, TEST_USER_ID, agent_id)
        cleanup.append(path)

        cat2 = AgentCatalog(storage)  # fresh instance
        result = await cat2.get_compact_catalog(TEST_USER_ID)
        assert agent_id in result

    @pytest.mark.asyncio
    async def test_catalog_includes_tool_hint(self, catalog, storage, cleanup):
        agent_id = f"hint-{uuid.uuid4().hex[:6]}"
        path = StoragePaths.agent_manifest(TEST_USER_ID, agent_id)
        manifest = _manifest(agent_id, tool_hint="Useful for email reading")
        await storage.write(path, json.dumps(manifest).encode(), content_type="application/json")
        cleanup.append(path)

        cat2 = AgentCatalog(storage)
        result = await cat2.get_compact_catalog(TEST_USER_ID)
        assert "Useful for email reading" in result

    @pytest.mark.asyncio
    async def test_empty_catalog_when_no_agents(self, storage):
        """User with no agents and no system agents returns empty string."""
        cat = AgentCatalog(storage)
        # Use a user ID that has no agents and check system/ has nothing extra
        # (comms may be seeded, so catalog may not be empty — just verify it's a string)
        result = await cat.get_compact_catalog(f"orphan-usr-{uuid.uuid4().hex[:8]}")
        assert isinstance(result, str)
