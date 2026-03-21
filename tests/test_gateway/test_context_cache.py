"""tests.test_gateway.test_context_cache — Unit tests for ConversationContextCache.

Tests the Redis-backed context cache using a mock Redis client so no real
Redis connection is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from graphclaw.gateway.context_cache import ConversationContextCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock():
    """Return a mock that mimics redis.asyncio.Redis pipeline API.

    Pipeline operations (rpush, ltrim, expire) are called synchronously
    in the production code; only execute() is awaited.
    """
    pipe = MagicMock()
    pipe.rpush = MagicMock()
    pipe.ltrim = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1, 1])

    client = AsyncMock()
    client.pipeline = MagicMock(return_value=pipe)
    client.lrange = AsyncMock(return_value=[])
    client.delete = AsyncMock()
    client.aclose = AsyncMock()
    return client, pipe


# ---------------------------------------------------------------------------
# No-op mode (redis_client=None)
# ---------------------------------------------------------------------------


class TestContextCacheNoOp:
    async def test_append_noop(self):
        cache = ConversationContextCache(redis_client=None)
        # Should not raise
        await cache.append("SES-1", {"key": "val"})

    async def test_get_context_returns_empty(self):
        cache = ConversationContextCache(redis_client=None)
        result = await cache.get_context("SES-1")
        assert result == []

    async def test_clear_noop(self):
        cache = ConversationContextCache(redis_client=None)
        await cache.clear("SES-1")

    async def test_close_noop(self):
        cache = ConversationContextCache(redis_client=None)
        await cache.close()


# ---------------------------------------------------------------------------
# With Redis mock
# ---------------------------------------------------------------------------


class TestContextCacheWithRedis:
    async def test_append_calls_pipeline(self):
        client, pipe = _make_redis_mock()
        cache = ConversationContextCache(redis_client=client)

        await cache.append("SES-abc", {"message_id": "x", "body": "hello"})

        pipe.rpush.assert_called_once()
        pipe.ltrim.assert_called_once()
        pipe.expire.assert_called_once()
        pipe.execute.assert_called_once()

    async def test_append_uses_correct_key(self):
        client, pipe = _make_redis_mock()
        cache = ConversationContextCache(redis_client=client)

        await cache.append("SES-xyz", {"a": 1})

        args = pipe.rpush.call_args[0]
        assert args[0] == "graphclaw:ctx:SES-xyz"

    async def test_get_context_returns_parsed_json(self):
        import json

        client, pipe = _make_redis_mock()
        client.lrange = AsyncMock(
            return_value=[
                json.dumps({"body": "msg1"}),
                json.dumps({"body": "msg2"}),
            ]
        )
        cache = ConversationContextCache(redis_client=client)

        result = await cache.get_context("SES-abc")
        assert len(result) == 2
        assert result[0]["body"] == "msg1"
        assert result[1]["body"] == "msg2"

    async def test_get_context_empty_returns_empty(self):
        client, _ = _make_redis_mock()
        cache = ConversationContextCache(redis_client=client)

        result = await cache.get_context("SES-notexist")
        assert result == []

    async def test_clear_calls_delete(self):
        client, _ = _make_redis_mock()
        cache = ConversationContextCache(redis_client=client)

        await cache.clear("SES-abc")

        client.delete.assert_called_once_with("graphclaw:ctx:SES-abc")

    async def test_close_calls_aclose(self):
        client, _ = _make_redis_mock()
        cache = ConversationContextCache(redis_client=client)
        await cache.close()
        client.aclose.assert_called_once()

    async def test_redis_error_doesnt_raise(self):
        client, pipe = _make_redis_mock()
        pipe.execute = AsyncMock(side_effect=Exception("Redis connection lost"))
        cache = ConversationContextCache(redis_client=client)

        # Should silently swallow the error
        await cache.append("SES-abc", {"a": 1})

    async def test_get_context_error_returns_empty(self):
        client, _ = _make_redis_mock()
        client.lrange = AsyncMock(side_effect=Exception("timeout"))
        cache = ConversationContextCache(redis_client=client)

        result = await cache.get_context("SES-abc")
        assert result == []


# ---------------------------------------------------------------------------
# from_env factory
# ---------------------------------------------------------------------------


class TestContextCacheFromEnv:
    async def test_from_env_no_url_returns_noop(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        cache = await ConversationContextCache.from_env()
        assert cache._redis is None

    async def test_from_env_no_redis_package_returns_noop(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        import sys

        with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}):
            cache = await ConversationContextCache.from_env()
            assert cache._redis is None
