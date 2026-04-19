"""Integration tests for gateway seeding — requires live MinIO.

Verifies that seed_system_content() correctly seeds all required objects
and is idempotent (does not overwrite existing content).

Run with::

    pytest tests/test_gateway/test_seeding_integration.py -m integration
"""

from __future__ import annotations

import json
import os

import pytest

from graphclaw.agent.knowledge import KNOWN_TOPICS
from graphclaw.gateway.seeding import seed_system_content
from graphclaw.infra.storage import S3StorageClient, StoragePaths

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Connection constants
# ---------------------------------------------------------------------------

BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def storage():
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


# ---------------------------------------------------------------------------
# Seeding — content written
# ---------------------------------------------------------------------------


class TestSeedSystemContent:
    @pytest.mark.asyncio
    async def test_system_header_seeded(self, storage):
        await seed_system_content(storage)
        path = StoragePaths.system_prompt_header()
        exists = await storage.exists(path)
        assert exists, f"Expected {path} to exist after seeding"

    @pytest.mark.asyncio
    async def test_system_header_has_content(self, storage):
        await seed_system_content(storage)
        raw = await storage.read(StoragePaths.system_prompt_header())
        assert len(raw) > 100  # not empty

    @pytest.mark.asyncio
    async def test_all_knowledge_files_seeded(self, storage):
        await seed_system_content(storage)
        for topic in KNOWN_TOPICS:
            path = StoragePaths.system_knowledge(topic)
            exists = await storage.exists(path)
            assert exists, f"Expected knowledge topic '{topic}' at {path}"

    @pytest.mark.asyncio
    async def test_knowledge_files_have_content(self, storage):
        await seed_system_content(storage)
        for topic in KNOWN_TOPICS:
            raw = await storage.read(StoragePaths.system_knowledge(topic))
            assert len(raw) > 50, f"Knowledge topic '{topic}' appears empty"

    @pytest.mark.asyncio
    async def test_comms_profile_seeded(self, storage):
        await seed_system_content(storage)
        exists = await storage.exists(StoragePaths.system_agent_profile("comms"))
        assert exists

    @pytest.mark.asyncio
    async def test_comms_manifest_seeded(self, storage):
        await seed_system_content(storage)
        path = StoragePaths.system_agent_manifest("comms")
        exists = await storage.exists(path)
        assert exists

    @pytest.mark.asyncio
    async def test_comms_manifest_valid_json(self, storage):
        await seed_system_content(storage)
        raw = await storage.read(StoragePaths.system_agent_manifest("comms"))
        manifest = json.loads(raw.decode())
        assert manifest["agent_id"] == "comms"
        assert manifest["type"] == "system"
        assert "capabilities" in manifest

    @pytest.mark.asyncio
    async def test_comms_config_seeded(self, storage):
        await seed_system_content(storage)
        exists = await storage.exists(StoragePaths.system_agent_config("comms"))
        assert exists


# ---------------------------------------------------------------------------
# Seeding — idempotency
# ---------------------------------------------------------------------------


class TestSeedIdempotency:
    @pytest.mark.asyncio
    async def test_existing_content_not_overwritten(self, storage):
        """seed_system_content should skip objects that already exist."""
        path = StoragePaths.system_prompt_header()

        # Ensure header exists from first seeding run
        await seed_system_content(storage)
        original_content = await storage.read(path)

        # Manually modify the header
        modified = b"# Modified header - should not be overwritten"
        await storage.write(path, modified, content_type="text/markdown")

        # Seed again — should NOT overwrite
        await seed_system_content(storage)

        after_reseed = await storage.read(path)
        assert after_reseed == modified, (
            "seed_system_content overwrote existing content — idempotency violated"
        )

        # Restore original content for other tests
        await storage.write(path, original_content, content_type="text/markdown")

    @pytest.mark.asyncio
    async def test_idempotent_for_knowledge_files(self, storage):
        """Knowledge files should not be overwritten on re-seed."""
        topic = KNOWN_TOPICS[0]
        path = StoragePaths.system_knowledge(topic)

        await seed_system_content(storage)
        original = await storage.read(path)

        # Modify one knowledge file
        modified = b"# Modified content - should survive re-seed"
        await storage.write(path, modified, content_type="text/markdown")

        await seed_system_content(storage)
        after = await storage.read(path)
        assert after == modified

        # Restore
        await storage.write(path, original, content_type="text/markdown")

    @pytest.mark.asyncio
    async def test_seed_completes_without_raising(self, storage):
        """seed_system_content should not raise even when called multiple times."""
        await seed_system_content(storage)
        await seed_system_content(storage)  # Second call — no exception
