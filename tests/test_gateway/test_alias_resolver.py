"""tests.test_gateway.test_alias_resolver — Unit tests for AliasResolver.

Tests cross-channel identity resolution using a mock Redis client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from graphclaw.gateway.alias_resolver import AliasResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock():
    """Return a mock that mimics redis.asyncio.Redis pipeline + get/set/smembers.

    Pipeline operations (set, sadd, delete, srem) are called synchronously
    in the production code; only execute() is awaited.
    """
    pipe = MagicMock()
    pipe.set = MagicMock()
    pipe.sadd = MagicMock()
    pipe.delete = MagicMock()
    pipe.srem = MagicMock()
    pipe.execute = AsyncMock(return_value=[True, True])

    client = AsyncMock()
    client.pipeline = MagicMock(return_value=pipe)
    client.get = AsyncMock(return_value=None)
    client.smembers = AsyncMock(return_value=set())
    client.aclose = AsyncMock()
    return client, pipe


# ---------------------------------------------------------------------------
# No-op mode
# ---------------------------------------------------------------------------


class TestAliasResolverNoOp:
    async def test_resolve_returns_none(self):
        resolver = AliasResolver(redis_client=None)
        result = await resolver.resolve("whatsapp", "15551234567")
        assert result is None

    async def test_register_noop(self):
        resolver = AliasResolver(redis_client=None)
        await resolver.register("whatsapp", "15551234567", "USER-abc")

    async def test_get_aliases_returns_empty(self):
        resolver = AliasResolver(redis_client=None)
        result = await resolver.get_aliases("USER-abc")
        assert result == []

    async def test_deregister_noop(self):
        resolver = AliasResolver(redis_client=None)
        await resolver.deregister("whatsapp", "15551234567")

    async def test_close_noop(self):
        resolver = AliasResolver(redis_client=None)
        await resolver.close()


# ---------------------------------------------------------------------------
# With Redis mock
# ---------------------------------------------------------------------------


class TestAliasResolverWithRedis:
    async def test_resolve_returns_user_id(self):
        client, _ = _make_redis_mock()
        client.get = AsyncMock(return_value="USER-abc-123")
        resolver = AliasResolver(redis_client=client)

        result = await resolver.resolve("whatsapp", "15551234567")
        assert result == "USER-abc-123"
        client.get.assert_called_once_with("graphclaw:alias:whatsapp:15551234567")

    async def test_resolve_returns_none_when_not_found(self):
        client, _ = _make_redis_mock()
        client.get = AsyncMock(return_value=None)
        resolver = AliasResolver(redis_client=client)

        result = await resolver.resolve("telegram", "987654")
        assert result is None

    async def test_register_creates_both_mappings(self):
        client, pipe = _make_redis_mock()
        resolver = AliasResolver(redis_client=client)

        await resolver.register("whatsapp", "15551234567", "USER-abc")

        pipe.set.assert_called_once_with("graphclaw:alias:whatsapp:15551234567", "USER-abc")
        pipe.sadd.assert_called_once_with("graphclaw:user_aliases:USER-abc", "whatsapp:15551234567")
        pipe.execute.assert_called_once()

    async def test_get_aliases_returns_sorted_list(self):
        client, _ = _make_redis_mock()
        client.smembers = AsyncMock(
            return_value={"telegram:999", "whatsapp:15551234567", "email:alice@example.com"}
        )
        resolver = AliasResolver(redis_client=client)

        aliases = await resolver.get_aliases("USER-abc")
        assert aliases == sorted(
            ["telegram:999", "whatsapp:15551234567", "email:alice@example.com"]
        )

    async def test_get_aliases_empty(self):
        client, _ = _make_redis_mock()
        resolver = AliasResolver(redis_client=client)

        aliases = await resolver.get_aliases("USER-nobody")
        assert aliases == []

    async def test_deregister_removes_both_mappings(self):
        client, pipe = _make_redis_mock()
        client.get = AsyncMock(return_value="USER-abc")
        resolver = AliasResolver(redis_client=client)

        await resolver.deregister("whatsapp", "15551234567")

        pipe.delete.assert_called_once_with("graphclaw:alias:whatsapp:15551234567")
        pipe.srem.assert_called_once_with("graphclaw:user_aliases:USER-abc", "whatsapp:15551234567")

    async def test_deregister_noop_if_no_user_found(self):
        client, pipe = _make_redis_mock()
        client.get = AsyncMock(return_value=None)
        resolver = AliasResolver(redis_client=client)

        await resolver.deregister("whatsapp", "unknown")
        pipe.delete.assert_not_called()

    async def test_redis_error_doesnt_raise(self):
        client, _ = _make_redis_mock()
        client.get = AsyncMock(side_effect=Exception("connection refused"))
        resolver = AliasResolver(redis_client=client)

        result = await resolver.resolve("whatsapp", "123")
        assert result is None

    async def test_close_calls_aclose(self):
        client, _ = _make_redis_mock()
        resolver = AliasResolver(redis_client=client)
        await resolver.close()
        client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# from_env factory
# ---------------------------------------------------------------------------


class TestAliasResolverFromEnv:
    async def test_from_env_no_url_returns_noop(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        resolver = await AliasResolver.from_env()
        assert resolver._redis is None

    async def test_from_env_no_redis_package_returns_noop(self, monkeypatch):
        import sys

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}):
            resolver = await AliasResolver.from_env()
            assert resolver._redis is None
