# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for MainOrchestrator caching — system header and user profile.

Verifies:
- _load_system_header() caches the result in-process with a 1-hour TTL.
- _load_agent_profile() caches per-user in Redis with a 15-minute TTL.
- invalidate_user_profile() evicts the Redis key.
- All cache operations degrade gracefully when Redis is unavailable.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator

# ---------------------------------------------------------------------------
# Minimal constructor helpers
# ---------------------------------------------------------------------------


def _make_storage(header_content: bytes = b"# Header", profile_content: bytes = b"# Profile"):
    storage = MagicMock()

    async def _read(path: str) -> bytes:
        if "system_header" in path or "header" in path:
            return header_content
        if "profile" in path:
            return profile_content
        raise FileNotFoundError(path)

    storage.read = AsyncMock(side_effect=_read)
    storage.list_objects = AsyncMock(return_value=[])
    return storage


def _make_redis(profile_cached: str | None = None) -> MagicMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=profile_cached)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    return redis


def _make_orchestrator(storage=None, redis=None) -> MainOrchestrator:
    graph_repo = MagicMock()
    scoring_engine = MagicMock()
    state_machine = MagicMock()
    return MainOrchestrator(
        graph_repo=graph_repo,
        scoring_engine=scoring_engine,
        state_machine=state_machine,
        storage_client=storage,
        redis_client=redis,
    )


# ---------------------------------------------------------------------------
# _load_system_header — in-process TTL cache
# ---------------------------------------------------------------------------


class TestSystemHeaderCache:
    @pytest.mark.asyncio
    async def test_first_call_reads_minio(self):
        storage = _make_storage()
        orch = _make_orchestrator(storage=storage)

        await orch._load_system_header()

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_is_cache_hit(self):
        storage = _make_storage()
        orch = _make_orchestrator(storage=storage)

        await orch._load_system_header()
        await orch._load_system_header()

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_many_calls_only_one_minio_read(self):
        storage = _make_storage()
        orch = _make_orchestrator(storage=storage)

        for _ in range(20):
            await orch._load_system_header()

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_ttl_causes_refresh(self):
        storage = _make_storage()
        orch = _make_orchestrator(storage=storage)

        await orch._load_system_header()
        # Wind back timestamp past TTL
        orch._system_header_at = time.monotonic() - MainOrchestrator._SYSTEM_HEADER_TTL - 1

        await orch._load_system_header()

        assert storage.read.call_count == 2

    @pytest.mark.asyncio
    async def test_within_ttl_no_refresh(self):
        storage = _make_storage()
        orch = _make_orchestrator(storage=storage)

        await orch._load_system_header()
        # Within TTL
        orch._system_header_at = time.monotonic() - (MainOrchestrator._SYSTEM_HEADER_TTL / 2)

        await orch._load_system_header()

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_storage_error_returns_fallback(self):
        storage = MagicMock()
        storage.read = AsyncMock(side_effect=Exception("MinIO down"))
        orch = _make_orchestrator(storage=storage)

        result = await orch._load_system_header()

        assert "GraphClaw" in result  # hardcoded default contains "GraphClaw"

    @pytest.mark.asyncio
    async def test_fallback_result_is_also_cached(self):
        storage = MagicMock()
        storage.read = AsyncMock(side_effect=Exception("MinIO down"))
        orch = _make_orchestrator(storage=storage)

        await orch._load_system_header()
        await orch._load_system_header()

        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_storage_returns_fallback(self):
        orch = _make_orchestrator(storage=None)

        result = await orch._load_system_header()

        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_cached_value_matches_storage_content(self):
        storage = _make_storage(header_content=b"# My Custom Header")
        orch = _make_orchestrator(storage=storage)

        result = await orch._load_system_header()

        assert result == "# My Custom Header"

    @pytest.mark.asyncio
    async def test_second_call_returns_same_content(self):
        storage = _make_storage(header_content=b"# Consistent Header")
        orch = _make_orchestrator(storage=storage)

        r1 = await orch._load_system_header()
        r2 = await orch._load_system_header()

        assert r1 == r2 == "# Consistent Header"


# ---------------------------------------------------------------------------
# _load_agent_profile — Redis Tier 2 cache
# ---------------------------------------------------------------------------


class TestAgentProfileRedisCache:
    @pytest.mark.asyncio
    async def test_redis_hit_skips_minio(self):
        storage = _make_storage()
        redis = _make_redis(profile_cached="# Cached Profile")
        orch = _make_orchestrator(storage=storage, redis=redis)

        result = await orch._load_agent_profile("usr-001")

        assert result == "# Cached Profile"
        storage.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_miss_reads_minio_and_stores(self):
        storage = _make_storage(profile_content=b"# Live Profile")
        redis = _make_redis(profile_cached=None)
        orch = _make_orchestrator(storage=storage, redis=redis)

        result = await orch._load_agent_profile("usr-001")

        assert result == "# Live Profile"
        storage.read.assert_called_once()
        redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_key_is_user_scoped(self):
        storage = _make_storage()
        redis = _make_redis(profile_cached="# Profile")
        orch = _make_orchestrator(storage=storage, redis=redis)

        await orch._load_agent_profile("usr-abc")

        redis.get.assert_called_once_with(f"{MainOrchestrator._USER_PROFILE_KEY_PREFIX}usr-abc")

    @pytest.mark.asyncio
    async def test_redis_setex_uses_correct_ttl(self):
        storage = _make_storage(profile_content=b"# Profile")
        redis = _make_redis(profile_cached=None)
        orch = _make_orchestrator(storage=storage, redis=redis)

        await orch._load_agent_profile("usr-001")

        call_args = redis.setex.call_args[0]
        assert call_args[1] == MainOrchestrator._USER_PROFILE_REDIS_TTL

    @pytest.mark.asyncio
    async def test_different_users_use_different_keys(self):
        storage = _make_storage()
        redis = _make_redis(profile_cached="# Profile")
        orch = _make_orchestrator(storage=storage, redis=redis)

        await orch._load_agent_profile("usr-aaa")
        await orch._load_agent_profile("usr-bbb")

        keys_queried = [c[0][0] for c in redis.get.call_args_list]
        assert f"{MainOrchestrator._USER_PROFILE_KEY_PREFIX}usr-aaa" in keys_queried
        assert f"{MainOrchestrator._USER_PROFILE_KEY_PREFIX}usr-bbb" in keys_queried

    @pytest.mark.asyncio
    async def test_no_redis_reads_minio_directly(self):
        storage = _make_storage(profile_content=b"# Direct Profile")
        orch = _make_orchestrator(storage=storage, redis=None)

        result = await orch._load_agent_profile("usr-001")

        assert result == "# Direct Profile"
        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_get_error_falls_back_to_minio(self):
        storage = _make_storage(profile_content=b"# Fallback Profile")
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=Exception("Redis timeout"))
        redis.setex = AsyncMock()
        orch = _make_orchestrator(storage=storage, redis=redis)

        result = await orch._load_agent_profile("usr-001")

        assert result == "# Fallback Profile"
        storage.read.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_set_error_does_not_raise(self):
        storage = _make_storage()
        redis = _make_redis(profile_cached=None)
        redis.setex = AsyncMock(side_effect=Exception("Redis write failed"))
        orch = _make_orchestrator(storage=storage, redis=redis)

        # Should not raise
        await orch._load_agent_profile("usr-001")

    @pytest.mark.asyncio
    async def test_minio_error_returns_empty_string(self):
        storage = MagicMock()
        storage.read = AsyncMock(side_effect=FileNotFoundError("profile.md"))
        redis = _make_redis(profile_cached=None)
        orch = _make_orchestrator(storage=storage, redis=redis)

        result = await orch._load_agent_profile("usr-001")

        assert result == ""

    @pytest.mark.asyncio
    async def test_no_storage_returns_empty_string(self):
        orch = _make_orchestrator(storage=None, redis=None)

        result = await orch._load_agent_profile("usr-001")

        assert result == ""


# ---------------------------------------------------------------------------
# invalidate_user_profile
# ---------------------------------------------------------------------------


class TestInvalidateUserProfile:
    @pytest.mark.asyncio
    async def test_evicts_redis_key(self):
        storage = _make_storage()
        redis = _make_redis()
        orch = _make_orchestrator(storage=storage, redis=redis)

        await orch.invalidate_user_profile("usr-001")

        redis.delete.assert_called_once_with(f"{MainOrchestrator._USER_PROFILE_KEY_PREFIX}usr-001")

    @pytest.mark.asyncio
    async def test_no_redis_does_nothing(self):
        orch = _make_orchestrator(storage=None, redis=None)

        # Should not raise
        await orch.invalidate_user_profile("usr-001")

    @pytest.mark.asyncio
    async def test_redis_delete_error_does_not_raise(self):
        storage = _make_storage()
        redis = _make_redis()
        redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        orch = _make_orchestrator(storage=storage, redis=redis)

        await orch.invalidate_user_profile("usr-001")

    @pytest.mark.asyncio
    async def test_invalidate_causes_next_call_to_hit_minio(self):
        storage = _make_storage(profile_content=b"# Updated Profile")
        redis = _make_redis(profile_cached="# Old Profile")
        orch = _make_orchestrator(storage=storage, redis=redis)

        # First call hits Redis cache
        r1 = await orch._load_agent_profile("usr-001")
        assert r1 == "# Old Profile"
        storage.read.assert_not_called()

        # Invalidate
        await orch.invalidate_user_profile("usr-001")

        # Simulate Redis returning None after eviction
        redis.get = AsyncMock(return_value=None)

        # Next call re-reads MinIO
        r2 = await orch._load_agent_profile("usr-001")
        assert r2 == "# Updated Profile"
        storage.read.assert_called_once()
