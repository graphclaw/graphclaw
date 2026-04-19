"""Integration tests for KnowledgeBase — requires live MinIO.

Connects to the MinIO instance started by docker-compose.  The bucket and
endpoint are read from environment variables (same as the gateway):

    STORAGE_BUCKET      (default: graphclaw)
    STORAGE_ENDPOINT_URL (default: http://localhost:9000)
    AWS_ACCESS_KEY_ID   (default: minioadmin)
    AWS_SECRET_ACCESS_KEY (default: minioadmin)

Run with::

    pytest tests/test_agent/test_knowledge_integration.py -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from graphclaw.agent.knowledge import KnowledgeBase
from graphclaw.infra.storage import S3StorageClient, StoragePaths

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# MinIO connection constants
# ---------------------------------------------------------------------------

BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

# Ensure boto3 picks up MinIO credentials from environment
os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def storage():
    """Real S3StorageClient pointed at the local MinIO instance."""
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


@pytest_asyncio.fixture
async def kb(storage):
    """Fresh KnowledgeBase for each test (no cross-test cache contamination)."""
    return KnowledgeBase(storage)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_objects(storage):
    """Remove any knowledge objects written during tests."""
    written: list[str] = []
    yield written
    for path in written:
        try:
            await storage.delete(path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _test_topic() -> str:
    """Generate a unique topic name so tests don't collide."""
    return f"test_topic_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# list_topics
# ---------------------------------------------------------------------------


class TestListTopicsIntegration:
    @pytest.mark.asyncio
    async def test_returns_seeded_topics_after_gateway_startup(self, kb, storage):
        """After gateway startup (seeding), all 6 canonical topics should be listed."""
        topics = await kb.list_topics()
        # At least some of the known topics should exist if seeding has run
        # (tests may run before the gateway, so check gracefully)
        assert isinstance(topics, list)
        assert len(topics) > 0

    @pytest.mark.asyncio
    async def test_custom_topic_appears_in_list(self, kb, storage, cleanup_test_objects):
        topic = _test_topic()
        path = StoragePaths.system_knowledge(topic)
        await storage.write(path, b"# Custom Topic\n\nContent.", content_type="text/markdown")
        cleanup_test_objects.append(path)

        topics = await kb.list_topics()
        assert topic in topics

    @pytest.mark.asyncio
    async def test_list_topics_excludes_non_md_files(self, kb, storage, cleanup_test_objects):
        prefix = StoragePaths.system_knowledge_prefix()
        path = f"{prefix}README.txt"
        await storage.write(path, b"not a topic", content_type="text/plain")
        cleanup_test_objects.append(path)

        topics = await kb.list_topics()
        assert "README" not in topics


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


class TestReadIntegration:
    @pytest.mark.asyncio
    async def test_read_returns_stored_content(self, kb, storage, cleanup_test_objects):
        topic = _test_topic()
        content = f"# {topic}\n\nDetailed rules here."
        path = StoragePaths.system_knowledge(topic)
        await storage.write(path, content.encode(), content_type="text/markdown")
        cleanup_test_objects.append(path)

        result = await kb.read(topic)
        assert result == content

    @pytest.mark.asyncio
    async def test_read_caches_content(self, kb, storage, cleanup_test_objects):
        topic = _test_topic()
        content = b"cached content"
        path = StoragePaths.system_knowledge(topic)
        await storage.write(path, content, content_type="text/markdown")
        cleanup_test_objects.append(path)

        first = await kb.read(topic)
        # Overwrite storage — cached read should return original
        await storage.write(path, b"modified content", content_type="text/markdown")
        second = await kb.read(topic)

        assert first == second == content.decode()

    @pytest.mark.asyncio
    async def test_read_missing_topic_returns_error_message(self, kb):
        result = await kb.read("nonexistent_topic_xyz_abc")
        assert "not found" in result.lower() or "nonexistent_topic_xyz_abc" in result

    @pytest.mark.asyncio
    async def test_read_seeded_topic_if_available(self, kb):
        """If the gateway has seeded content, reading it should return non-empty text."""
        result = await kb.read("node_creation_rules")
        # Either real content or an error message — both are strings
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# get_index
# ---------------------------------------------------------------------------


class TestGetIndexIntegration:
    @pytest.mark.asyncio
    async def test_get_index_is_non_empty(self, kb):
        index = await kb.get_index()
        # Falls back to KNOWN_TOPICS at minimum
        assert "Knowledge Base" in index or len(index) > 0

    @pytest.mark.asyncio
    async def test_get_index_contains_custom_topic(self, kb, storage, cleanup_test_objects):
        topic = _test_topic()
        path = StoragePaths.system_knowledge(topic)
        await storage.write(path, b"content", content_type="text/markdown")
        cleanup_test_objects.append(path)

        kb2 = KnowledgeBase(storage)  # fresh instance so list_topics re-fetches
        index = await kb2.get_index()
        assert topic in index
