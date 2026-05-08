# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_infra.test_broker — Unit tests for MessageBroker / RedisMessageBroker.

Description
-----------
Tests for the ``RedisMessageBroker`` implementation using a mocked
``redis.asyncio`` client.  No real Redis connection is required.

Design Patterns
---------------
- Arrange/Act/Assert: Each test injects a mock Redis client and verifies the
  expected Redis commands are issued.
- AsyncMock: All Redis coroutine methods are replaced with AsyncMock so they
  can be awaited in async tests.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.infra.broker: RedisMessageBroker and queue name constants.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from graphclaw.infra.broker import (
    INBOUND_MESSAGES,
    OUTBOUND_MESSAGES,
    SKILL_JOBS,
    STATUS_UPDATES,
    TRIGGER_EVENTS,
    MessageBroker,
    RedisMessageBroker,
)


def _make_broker(mock_redis: object) -> RedisMessageBroker:
    """Return a RedisMessageBroker with *mock_redis* pre-injected."""
    broker = RedisMessageBroker(url="redis://localhost:6379")
    broker._redis = mock_redis
    return broker


# ---------------------------------------------------------------------------
# test_publish_calls_lpush
# ---------------------------------------------------------------------------


async def test_publish_calls_lpush() -> None:
    mock_redis = AsyncMock()
    broker = _make_broker(mock_redis)

    await broker.publish(INBOUND_MESSAGES, '{"task_id": "T1"}')

    mock_redis.lpush.assert_awaited_once_with(INBOUND_MESSAGES, '{"task_id": "T1"}')


# ---------------------------------------------------------------------------
# test_consume_yields_messages
# ---------------------------------------------------------------------------


async def test_consume_yields_messages() -> None:
    mock_redis = AsyncMock()
    # First call returns a message, second call returns None (timeout), which
    # causes the generator to loop; we stop after the first yielded value.
    mock_redis.brpop.side_effect = [
        (INBOUND_MESSAGES, '{"task_id": "T1"}'),
        None,
        (INBOUND_MESSAGES, '{"task_id": "T2"}'),
    ]
    broker = _make_broker(mock_redis)

    messages: list[str] = []
    async for msg in broker.consume(INBOUND_MESSAGES):
        messages.append(msg)
        if len(messages) >= 2:
            break

    assert messages == ['{"task_id": "T1"}', '{"task_id": "T2"}']


# ---------------------------------------------------------------------------
# test_acknowledge_is_noop
# ---------------------------------------------------------------------------


async def test_acknowledge_is_noop() -> None:
    mock_redis = AsyncMock()
    broker = _make_broker(mock_redis)

    # Should complete without error and not call any Redis method
    await broker.acknowledge(INBOUND_MESSAGES, "some-message-id")

    mock_redis.assert_not_called()


# ---------------------------------------------------------------------------
# test_close_closes_connection
# ---------------------------------------------------------------------------


async def test_close_closes_connection() -> None:
    mock_redis = AsyncMock()
    broker = _make_broker(mock_redis)

    await broker.close()

    mock_redis.aclose.assert_awaited_once()
    assert broker._redis is None


async def test_close_when_not_connected_is_safe() -> None:
    broker = RedisMessageBroker()
    # Should not raise even if _redis was never initialised
    await broker.close()
    assert broker._redis is None


# ---------------------------------------------------------------------------
# test_queue_name_constants
# ---------------------------------------------------------------------------


def test_queue_name_constants() -> None:
    assert INBOUND_MESSAGES == "inbound_messages"
    assert TRIGGER_EVENTS == "trigger_events"
    assert SKILL_JOBS == "skill_jobs"
    assert STATUS_UPDATES == "status_updates"
    assert OUTBOUND_MESSAGES == "outbound_messages"


# ---------------------------------------------------------------------------
# test_message_broker_is_abstract
# ---------------------------------------------------------------------------


def test_message_broker_is_abstract() -> None:
    with pytest.raises(TypeError):
        MessageBroker()  # type: ignore[abstract]
