# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for AgentCatalog two-tier caching behaviour.

Tier 1 — in-process TTL for system manifests:
  Verifies system manifests are cached in-process and the TTL causes a refresh.

Tier 2 — Redis for user manifests:
  Verifies user manifests are read from Redis on cache hit, written to Redis on
  cache miss, and that invalidate_user_catalog() evicts the key.

Graceful degradation:
  Verifies that all operations fall back to MinIO when Redis is unavailable.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.catalog import (
    _SYSTEM_MANIFESTS_TTL,
    _USER_CATALOG_KEY_PREFIX,
    _USER_CATALOG_TTL,
    AgentCatalog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = "usr-test-001"


def _manifest(agent_id: str, agent_type: str = "user") -> dict:
    return {
        "agent_id": agent_id,
        "type": agent_type,
        "name": f"{agent_id} Agent",
        "description": f"Test {agent_id}",
        "capabilities": [],
        "invocation": "async",
        "tool_hint": f"Use for {agent_id}.",
    }


def _make_storage(
    system_manifests: list[dict] | None = None,
    user_manifests: list[dict] | None = None,
) -> MagicMock:
    """Return a StorageClient mock that serves the given manifests."""
    if system_manifests is None:
        system_manifests = [_manifest("comms", "system")]
    if user_manifests is None:
        user_manifests = [_manifest("my-agent", "user")]

    sys_keys = [f"system/agents/{m['agent_id']}/manifest.json" for m in system_manifests]
    usr_keys = [f"{_USER_ID}/agents/{m['agent_id']}/manifest.json" for m in user_manifests]

    all_manifests_by_key = {}
    for m in system_manifests:
        all_manifests_by_key[f"system/agents/{m['agent_id']}/manifest.json"] = m
    for m in user_manifests:
        all_manifests_by_key[f"{_USER_ID}/agents/{m['agent_id']}/manifest.json"] = m

    async def _list_objects(prefix: str) -> list[str]:
        if prefix.startswith("system/agents/"):
            return sys_keys
        return [k for k in usr_keys if k.startswith(prefix)]

    async def _read(key: str) -> bytes:
        if key in all_manifests_by_key:
            return json.dumps(all_manifests_by_key[key]).encode()
        raise FileNotFoundError(key)

    storage = MagicMock()
    storage.list_objects = AsyncMock(side_effect=_list_objects)
    storage.read = AsyncMock(side_effect=_read)
    return storage


def _make_redis(cached_value: str | None = None) -> MagicMock:
    """Return a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_value)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# Tier 1: in-process TTL — system manifests
# ---------------------------------------------------------------------------


class TestSystemManifestsTTLCache:
    @pytest.mark.asyncio
    async def test_first_call_hits_storage(self):
        storage = _make_storage()
        catalog = AgentCatalog(storage)

        await catalog._get_system_manifests()

        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_is_in_process_cache_hit(self):
        storage = _make_storage()
        catalog = AgentCatalog(storage)

        await catalog._get_system_manifests()
        await catalog._get_system_manifests()

        # Storage called only once despite two calls
        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_ttl_causes_refresh(self):
        storage = _make_storage()
        catalog = AgentCatalog(storage)

        await catalog._get_system_manifests()
        # Simulate TTL expiry by rewinding the timestamp
        catalog._system_manifests_at = time.monotonic() - _SYSTEM_MANIFESTS_TTL - 1

        await catalog._get_system_manifests()

        assert storage.list_objects.call_count == 2

    @pytest.mark.asyncio
    async def test_within_ttl_no_refresh(self):
        storage = _make_storage()
        catalog = AgentCatalog(storage)

        await catalog._get_system_manifests()
        # Simulate time passing but still within TTL
        catalog._system_manifests_at = time.monotonic() - (_SYSTEM_MANIFESTS_TTL / 2)

        await catalog._get_system_manifests()

        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_returned_manifests_match_storage(self):
        storage = _make_storage(system_manifests=[_manifest("comms", "system")])
        catalog = AgentCatalog(storage)

        result = await catalog._get_system_manifests()

        assert len(result) == 1
        assert result[0]["agent_id"] == "comms"

    @pytest.mark.asyncio
    async def test_separate_instances_do_not_share_cache(self):
        storage = _make_storage()
        cat1 = AgentCatalog(storage)
        cat2 = AgentCatalog(storage)

        await cat1._get_system_manifests()
        await cat2._get_system_manifests()

        # Two separate instances each load from storage
        assert storage.list_objects.call_count == 2


# ---------------------------------------------------------------------------
# Tier 2: Redis — user manifests
# ---------------------------------------------------------------------------


class TestUserManifestsRedisCache:
    @pytest.mark.asyncio
    async def test_redis_hit_skips_minio(self):
        storage = _make_storage()
        cached = [_manifest("cached-agent", "user")]
        redis = _make_redis(cached_value=json.dumps(cached))
        catalog = AgentCatalog(storage, redis_client=redis)

        result = await catalog._get_user_manifests(_USER_ID)

        assert result == cached
        # MinIO never touched when Redis has the value
        assert storage.list_objects.call_count == 0

    @pytest.mark.asyncio
    async def test_redis_miss_reads_minio_and_stores(self):
        storage = _make_storage(user_manifests=[_manifest("my-agent", "user")])
        redis = _make_redis(cached_value=None)
        catalog = AgentCatalog(storage, redis_client=redis)

        result = await catalog._get_user_manifests(_USER_ID)

        assert len(result) == 1
        assert result[0]["agent_id"] == "my-agent"
        # Result stored in Redis
        redis.setex.assert_called_once()
        call_args = redis.setex.call_args
        assert call_args[0][0] == f"{_USER_CATALOG_KEY_PREFIX}{_USER_ID}"
        assert call_args[0][1] == _USER_CATALOG_TTL

    @pytest.mark.asyncio
    async def test_redis_key_uses_user_id(self):
        storage = _make_storage()
        redis = _make_redis(cached_value=json.dumps([]))
        catalog = AgentCatalog(storage, redis_client=redis)

        await catalog._get_user_manifests(_USER_ID)

        redis.get.assert_called_once_with(f"{_USER_CATALOG_KEY_PREFIX}{_USER_ID}")

    @pytest.mark.asyncio
    async def test_no_redis_falls_back_to_minio(self):
        storage = _make_storage(user_manifests=[_manifest("my-agent", "user")])
        catalog = AgentCatalog(storage, redis_client=None)

        result = await catalog._get_user_manifests(_USER_ID)

        assert len(result) == 1
        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_get_error_falls_back_to_minio(self):
        storage = _make_storage(user_manifests=[_manifest("my-agent", "user")])
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=Exception("Redis timeout"))
        redis.setex = AsyncMock()
        catalog = AgentCatalog(storage, redis_client=redis)

        result = await catalog._get_user_manifests(_USER_ID)

        assert len(result) == 1
        storage.list_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_set_error_does_not_raise(self):
        storage = _make_storage()
        redis = _make_redis(cached_value=None)
        redis.setex = AsyncMock(side_effect=Exception("Redis write failed"))
        catalog = AgentCatalog(storage, redis_client=redis)

        # Should not raise even if Redis write fails
        await catalog._get_user_manifests(_USER_ID)


# ---------------------------------------------------------------------------
# invalidate_user_catalog
# ---------------------------------------------------------------------------


class TestInvalidateUserCatalog:
    @pytest.mark.asyncio
    async def test_evicts_redis_key(self):
        storage = _make_storage()
        redis = _make_redis()
        catalog = AgentCatalog(storage, redis_client=redis)

        await catalog.invalidate_user_catalog(_USER_ID)

        redis.delete.assert_called_once_with(f"{_USER_CATALOG_KEY_PREFIX}{_USER_ID}")

    @pytest.mark.asyncio
    async def test_no_redis_does_nothing(self):
        storage = _make_storage()
        catalog = AgentCatalog(storage, redis_client=None)

        # Should not raise
        await catalog.invalidate_user_catalog(_USER_ID)

    @pytest.mark.asyncio
    async def test_redis_delete_error_does_not_raise(self):
        storage = _make_storage()
        redis = _make_redis()
        redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        catalog = AgentCatalog(storage, redis_client=redis)

        await catalog.invalidate_user_catalog(_USER_ID)

    @pytest.mark.asyncio
    async def test_invalidate_causes_next_call_to_hit_minio(self):
        storage = _make_storage()
        redis = _make_redis(cached_value=None)
        catalog = AgentCatalog(storage, redis_client=redis)

        # Populate cache
        await catalog._get_user_manifests(_USER_ID)
        assert redis.setex.call_count == 1

        # Invalidate
        await catalog.invalidate_user_catalog(_USER_ID)

        # Simulate Redis returning None after eviction
        redis.get = AsyncMock(return_value=None)

        # Next call re-reads MinIO
        await catalog._get_user_manifests(_USER_ID)
        assert storage.list_objects.call_count == 2


# ---------------------------------------------------------------------------
# resolve_source — cache-aware fast path
# ---------------------------------------------------------------------------


class TestResolveSourceCache:
    @pytest.mark.asyncio
    async def test_resolve_uses_cached_system_manifests(self):
        storage = _make_storage(system_manifests=[_manifest("comms", "system")])
        catalog = AgentCatalog(storage)

        # Populate system cache
        await catalog._get_system_manifests()
        storage.list_objects.reset_mock()
        storage.read.reset_mock()

        result = await catalog.resolve_source(_USER_ID, "comms")

        assert result == "system"
        # No storage calls — used in-process cache
        storage.list_objects.assert_not_called()
        storage.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_unknown_agent_returns_user(self):
        storage = _make_storage(system_manifests=[_manifest("comms", "system")])
        catalog = AgentCatalog(storage)

        await catalog._get_system_manifests()

        result = await catalog.resolve_source(_USER_ID, "nonexistent-agent")

        assert result == "user"


# ---------------------------------------------------------------------------
# get_compact_catalog — end-to-end with both tiers
# ---------------------------------------------------------------------------


class TestGetCompactCatalogCaching:
    @pytest.mark.asyncio
    async def test_catalog_string_contains_system_and_user_agents(self):
        storage = _make_storage(
            system_manifests=[_manifest("comms", "system")],
            user_manifests=[_manifest("my-agent", "user")],
        )
        redis = _make_redis(cached_value=None)
        catalog = AgentCatalog(storage, redis_client=redis)

        result = await catalog.get_compact_catalog(_USER_ID)

        assert "comms [system]" in result
        assert "my-agent [user]" in result

    @pytest.mark.asyncio
    async def test_second_call_uses_in_process_cache_for_system(self):
        storage = _make_storage()
        redis = _make_redis(cached_value=json.dumps([_manifest("my-agent", "user")]))
        catalog = AgentCatalog(storage, redis_client=redis)

        await catalog.get_compact_catalog(_USER_ID)
        await catalog.get_compact_catalog(_USER_ID)

        # System manifests loaded once (in-process); user manifests from Redis each time
        assert storage.list_objects.call_count == 1


# ---------------------------------------------------------------------------
# get_compact_catalog — max_agents cap
#
# Regression coverage for the context-budget work: this catalog was
# previously unbounded, so a user with many agents paid its full cost on
# every turn regardless of relevance.
# ---------------------------------------------------------------------------


class TestGetCompactCatalogMaxAgentsCap:
    @pytest.mark.asyncio
    async def test_default_is_unbounded_backward_compatible(self):
        many = [_manifest(f"agent-{i}", "user") for i in range(20)]
        storage = _make_storage(system_manifests=[], user_manifests=many)
        catalog = AgentCatalog(storage)

        result = await catalog.get_compact_catalog(_USER_ID)

        for i in range(20):
            assert f"agent-{i}" in result
        assert "more —" not in result

    @pytest.mark.asyncio
    async def test_max_agents_caps_rendered_lines(self):
        many = [_manifest(f"agent-{i}", "user") for i in range(20)]
        storage = _make_storage(system_manifests=[], user_manifests=many)
        catalog = AgentCatalog(storage)

        result = await catalog.get_compact_catalog(_USER_ID, max_agents=5)

        for i in range(5):
            assert f"agent-{i}" in result
        for i in range(5, 20):
            assert f"agent-{i}" not in result

    @pytest.mark.asyncio
    async def test_max_agents_appends_remaining_count_hint(self):
        many = [_manifest(f"agent-{i}", "user") for i in range(20)]
        storage = _make_storage(system_manifests=[], user_manifests=many)
        catalog = AgentCatalog(storage)

        result = await catalog.get_compact_catalog(_USER_ID, max_agents=5)

        assert "(+15 more — call list_available_agents)" in result

    @pytest.mark.asyncio
    async def test_max_agents_larger_than_count_omits_hint(self):
        storage = _make_storage(
            system_manifests=[_manifest("comms", "system")],
            user_manifests=[_manifest("my-agent", "user")],
        )
        catalog = AgentCatalog(storage)

        result = await catalog.get_compact_catalog(_USER_ID, max_agents=10)

        assert "more —" not in result

    @pytest.mark.asyncio
    async def test_max_agents_preserves_delegation_footer(self):
        many = [_manifest(f"agent-{i}", "user") for i in range(5)]
        storage = _make_storage(system_manifests=[], user_manifests=many)
        catalog = AgentCatalog(storage)

        result = await catalog.get_compact_catalog(_USER_ID, max_agents=2)

        assert 'To delegate: load_tool_set("delegation"), then call delegate_to_agent' in result
