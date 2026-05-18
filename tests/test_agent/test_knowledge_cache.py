# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for KnowledgeBase caching behaviour.

Verifies that:
- list_topics() is only called once on storage (cached after first call).
- read(topic) is only called once on storage per topic (already cached).
- The _topics cache is populated from storage on first call.
- The _topics cache falls back to KNOWN_TOPICS on storage error.
- A second KnowledgeBase instance does NOT share the cache.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.knowledge import KNOWN_TOPICS, KnowledgeBase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage(topic_keys: list[str] | None = None, content: bytes = b"# content") -> MagicMock:
    """Return a mock StorageClient."""
    if topic_keys is None:
        topic_keys = [f"system/knowledge/{t}.md" for t in KNOWN_TOPICS[:2]]

    storage = MagicMock()
    storage.list_objects = AsyncMock(return_value=topic_keys)
    storage.read = AsyncMock(return_value=content)
    return storage


# ---------------------------------------------------------------------------
# list_topics caching
# ---------------------------------------------------------------------------


class TestListTopicsCache:
    @pytest.mark.asyncio
    async def test_first_call_hits_storage(self):
        storage = _make_storage()
        kb = KnowledgeBase(storage)

        await kb.list_topics()

        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_is_cache_hit(self):
        storage = _make_storage()
        kb = KnowledgeBase(storage)

        await kb.list_topics()
        await kb.list_topics()

        # Storage called exactly once despite two list_topics() calls
        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_many_calls_only_one_storage_read(self):
        storage = _make_storage()
        kb = KnowledgeBase(storage)

        for _ in range(10):
            await kb.list_topics()

        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_topics_extracted_from_keys(self):
        storage = _make_storage(
            topic_keys=[
                "system/knowledge/node_creation_rules.md",
                "system/knowledge/edge_creation_rules.md",
            ]
        )
        kb = KnowledgeBase(storage)

        topics = await kb.list_topics()

        assert "node_creation_rules" in topics
        assert "edge_creation_rules" in topics

    @pytest.mark.asyncio
    async def test_storage_error_falls_back_to_known_topics(self):
        storage = MagicMock()
        storage.list_objects = AsyncMock(side_effect=Exception("MinIO unavailable"))
        kb = KnowledgeBase(storage)

        topics = await kb.list_topics()

        assert topics == list(KNOWN_TOPICS)

    @pytest.mark.asyncio
    async def test_storage_error_result_is_also_cached(self):
        storage = MagicMock()
        storage.list_objects = AsyncMock(side_effect=Exception("MinIO unavailable"))
        kb = KnowledgeBase(storage)

        await kb.list_topics()
        await kb.list_topics()

        # Even the fallback result is cached — storage only called once
        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_separate_instances_do_not_share_cache(self):
        storage = _make_storage()
        kb1 = KnowledgeBase(storage)
        kb2 = KnowledgeBase(storage)

        await kb1.list_topics()
        await kb2.list_topics()

        # Two separate instances → two storage calls
        assert storage.list_objects.call_count == 2

    @pytest.mark.asyncio
    async def test_get_index_uses_cached_topics(self):
        """get_index() calls list_topics(); second get_index() should not re-read storage."""
        storage = _make_storage()
        kb = KnowledgeBase(storage)

        await kb.get_index()
        await kb.get_index()

        storage.list_objects.assert_called_once()


# ---------------------------------------------------------------------------
# read(topic) caching (pre-existing, regression guard)
# ---------------------------------------------------------------------------


class TestReadTopicCache:
    @pytest.mark.asyncio
    async def test_first_read_hits_storage(self):
        storage = _make_storage(content=b"# Node rules")
        kb = KnowledgeBase(storage)

        await kb.read("node_creation_rules")

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_read_same_topic_is_cache_hit(self):
        storage = _make_storage(content=b"# Node rules")
        kb = KnowledgeBase(storage)

        await kb.read("node_creation_rules")
        await kb.read("node_creation_rules")

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_different_topics_each_hit_storage_once(self):
        storage = _make_storage(content=b"# content")
        kb = KnowledgeBase(storage)

        await kb.read("node_creation_rules")
        await kb.read("edge_creation_rules")
        await kb.read("node_creation_rules")  # cache hit

        assert storage.read.call_count == 2
