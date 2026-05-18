# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for UserEventPublisher implementations.

Tests ``RedisUserEventPublisher`` against a live Redis instance and
``InMemoryUserEventPublisher`` for event ordering.

Run with::

    pytest tests/test_infra/test_user_events.py -m integration

Redis URL is read from REDIS_URL (default: redis://localhost:6379).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from graphclaw.agent.run_events import (
    AssistantDeltaPayload,
    RunCompletedPayload,
    RunEventType,
    RunStartedPayload,
    make_event,
    new_run_id,
)
from graphclaw.infra.user_events import (
    InMemoryUserEventPublisher,
    NullUserEventPublisher,
    RedisUserEventPublisher,
)

pytestmark = pytest.mark.integration

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_CHANNEL_PREFIX = "graphclaw:events:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def redis_client():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# InMemoryUserEventPublisher
# ---------------------------------------------------------------------------


class TestInMemoryPublisher:
    @pytest.mark.asyncio
    async def test_publish_stores_events(self):
        publisher = InMemoryUserEventPublisher()
        run_id = new_run_id()
        e1 = make_event(RunEventType.RUN_STARTED, run_id, "s", "user-1", 0, RunStartedPayload())
        e2 = make_event(
            RunEventType.ASSISTANT_DELTA,
            run_id,
            "s",
            "user-1",
            1,
            AssistantDeltaPayload(delta="hi"),
        )
        await publisher.publish("user-1", e1)
        await publisher.publish("user-1", e2)

        assert len(publisher.events) == 2
        assert publisher.events[0].event_type == RunEventType.RUN_STARTED
        assert publisher.events[1].event_type == RunEventType.ASSISTANT_DELTA

    @pytest.mark.asyncio
    async def test_events_for_filters_by_user(self):
        publisher = InMemoryUserEventPublisher()
        run_id = new_run_id()
        e_u1 = make_event(RunEventType.RUN_STARTED, run_id, "s", "user-1", 0, RunStartedPayload())
        e_u2 = make_event(RunEventType.RUN_STARTED, run_id, "s", "user-2", 0, RunStartedPayload())
        await publisher.publish("user-1", e_u1)
        await publisher.publish("user-2", e_u2)

        u1_events = publisher.events_for("user-1")
        u2_events = publisher.events_for("user-2")
        assert len(u1_events) == 1
        assert len(u2_events) == 1
        assert u1_events[0].user_id == "user-1"

    @pytest.mark.asyncio
    async def test_ordering_preserved_across_concurrent_publishes(self):
        """Multiple concurrent publishes must all land in order (asyncio is single-threaded)."""
        publisher = InMemoryUserEventPublisher()
        run_id = new_run_id()
        events = [
            make_event(
                RunEventType.ASSISTANT_DELTA,
                run_id,
                "s",
                "u",
                i,
                AssistantDeltaPayload(delta=str(i)),
            )
            for i in range(20)
        ]
        await asyncio.gather(*[publisher.publish("u", e) for e in events])
        seqs = [e.event_seq for e in publisher.events]
        # All events present (order of gather may vary, but all 20 must be there)
        assert set(seqs) == set(range(20))

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        publisher = InMemoryUserEventPublisher()
        await publisher.close()
        await publisher.close()  # Must not raise


# ---------------------------------------------------------------------------
# NullUserEventPublisher
# ---------------------------------------------------------------------------


class TestNullPublisher:
    @pytest.mark.asyncio
    async def test_publish_does_not_raise(self):
        publisher = NullUserEventPublisher()
        e = make_event(RunEventType.RUN_STARTED, new_run_id(), "", "u", 0, RunStartedPayload())
        await publisher.publish("u", e)  # must not raise

    @pytest.mark.asyncio
    async def test_close_does_not_raise(self):
        publisher = NullUserEventPublisher()
        await publisher.close()


# ---------------------------------------------------------------------------
# RedisUserEventPublisher
# ---------------------------------------------------------------------------


class TestRedisPublisher:
    @pytest.mark.asyncio
    async def test_publish_reaches_redis_channel(self, redis_client):
        """Published event must appear on the graphclaw:events:{user_id} channel."""
        user_id = f"test-pub-{new_run_id()[:8]}"
        channel = f"{_CHANNEL_PREFIX}{user_id}"

        publisher = RedisUserEventPublisher(redis_client)

        # Subscribe before publishing
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        run_id = new_run_id()
        event = make_event(
            RunEventType.RUN_STARTED,
            run_id,
            "ses-test",
            user_id,
            0,
            RunStartedPayload(message_preview="hello"),
        )

        await publisher.publish(user_id, event)

        # Read from pubsub with a timeout
        received = None
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                received = msg["data"]
                break

        await pubsub.unsubscribe(channel)
        await pubsub.aclose()

        assert received is not None, "No message received from Redis within timeout"
        parsed = json.loads(received)
        assert parsed["event_type"] == RunEventType.RUN_STARTED
        assert parsed["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_publish_multiple_events_in_order(self, redis_client):
        """Multiple events must arrive in publication order (Redis preserves FIFO per channel)."""
        user_id = f"test-multi-{new_run_id()[:8]}"
        channel = f"{_CHANNEL_PREFIX}{user_id}"

        publisher = RedisUserEventPublisher(redis_client)

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        run_id = new_run_id()
        n_events = 5
        for i in range(n_events):
            e = make_event(
                RunEventType.ASSISTANT_DELTA,
                run_id,
                "s",
                user_id,
                i,
                AssistantDeltaPayload(delta=f"chunk-{i}"),
            )
            await publisher.publish(user_id, e)

        received_seqs = []
        deadline = asyncio.get_event_loop().time() + 5.0
        while len(received_seqs) < n_events and asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                data = json.loads(msg["data"])
                received_seqs.append(data["event_seq"])

        await pubsub.unsubscribe(channel)
        await pubsub.aclose()

        assert len(received_seqs) == n_events
        assert received_seqs == list(range(n_events))

    @pytest.mark.asyncio
    async def test_publish_does_not_raise_on_close_error(self, redis_client):
        """close() must not raise even if the Redis client is already closed."""
        publisher = RedisUserEventPublisher(redis_client)
        await publisher.close()  # no-op but must not raise

    @pytest.mark.asyncio
    async def test_run_completed_event_round_trip(self, redis_client):
        """run.completed payload must survive the Redis round-trip intact."""
        user_id = f"test-completed-{new_run_id()[:8]}"
        channel = f"{_CHANNEL_PREFIX}{user_id}"
        publisher = RedisUserEventPublisher(redis_client)

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        event = make_event(
            RunEventType.RUN_COMPLETED,
            new_run_id(),
            "ses",
            user_id,
            3,
            RunCompletedPayload(
                input_tokens=200, output_tokens=100, tool_call_count=2, duration_ms=2500
            ),
        )
        await publisher.publish(user_id, event)

        received = None
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                received = json.loads(msg["data"])
                break

        await pubsub.unsubscribe(channel)
        await pubsub.aclose()

        assert received is not None
        payload = received["payload"]
        assert payload["input_tokens"] == 200
        assert payload["tool_call_count"] == 2
