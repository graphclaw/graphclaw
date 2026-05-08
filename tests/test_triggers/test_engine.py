# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.triggers.engine — TriggerEngine dispatch and loop behaviour.

Description
-----------
Verifies that the engine dispatches trigger events through the broker, deduplicates
events by idempotency key, handles on-demand triggers, and that the scheduled and
consumer loops function correctly with mocked collaborators.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from graphclaw.triggers.engine import TriggerEngine
from graphclaw.triggers.models import TriggerConfig, TriggerEvent, TriggerType
from graphclaw.triggers.scheduler import TriggerScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _make_mock_broker() -> MagicMock:
    """Return a MagicMock that satisfies the MessageBroker interface."""
    broker = MagicMock()
    broker.publish = AsyncMock()
    return broker


def _make_engine(broker=None, scheduler=None) -> TriggerEngine:
    broker = broker or _make_mock_broker()
    scheduler = scheduler or TriggerScheduler()
    return TriggerEngine(broker=broker, scheduler=scheduler)


# ---------------------------------------------------------------------------
# _dispatch — publish and idempotency
# ---------------------------------------------------------------------------


async def test_dispatch_publishes_to_broker() -> None:
    """_dispatch should call broker.publish with TRIGGER_EVENTS and JSON payload."""
    broker = _make_mock_broker()
    engine = _make_engine(broker=broker)

    event = TriggerEvent(
        trigger_id="TRIG-pub-1",
        trigger_type=TriggerType.ON_DEMAND,
        user_id="USER-1",
        created_at=_utc(2026, 3, 18, 8, 0),
    )
    result = await engine._dispatch(event)

    assert result is True
    broker.publish.assert_awaited_once()
    call_args = broker.publish.call_args
    queue_name = call_args[0][0]
    assert queue_name == "trigger_events"

    payload = json.loads(call_args[0][1])
    assert payload["trigger_id"] == "TRIG-pub-1"


async def test_dispatch_deduplicates_by_idempotency_key() -> None:
    """A second _dispatch with the same idempotency_key must return False."""
    broker = _make_mock_broker()
    engine = _make_engine(broker=broker)

    event = TriggerEvent(
        trigger_id="TRIG-dedup-1",
        trigger_type=TriggerType.TIME_BASED,
        user_id="USER-1",
        created_at=_utc(2026, 3, 18, 8, 0),
        idempotency_key="TC-1:2026-03-18",
    )

    first = await engine._dispatch(event)
    second = await engine._dispatch(event)

    assert first is True
    assert second is False
    assert broker.publish.await_count == 1


async def test_dispatch_no_idempotency_key_always_publishes() -> None:
    """Events with an empty idempotency_key are never deduplicated."""
    broker = _make_mock_broker()
    engine = _make_engine(broker=broker)

    for i in range(3):
        event = TriggerEvent(
            trigger_id=f"TRIG-{i}",
            trigger_type=TriggerType.ON_DEMAND,
            user_id="USER-1",
            created_at=_utc(2026, 3, 18, 8, 0),
            idempotency_key="",
        )
        await engine._dispatch(event)

    assert broker.publish.await_count == 3


# ---------------------------------------------------------------------------
# fire_on_demand
# ---------------------------------------------------------------------------


async def test_fire_on_demand_creates_event() -> None:
    """fire_on_demand should publish and return a TriggerEvent with ON_DEMAND type."""
    broker = _make_mock_broker()
    engine = _make_engine(broker=broker)

    event = await engine.fire_on_demand(user_id="USER-A", payload={"source": "cli"})

    assert isinstance(event, TriggerEvent)
    assert event.trigger_type == TriggerType.ON_DEMAND
    assert event.user_id == "USER-A"
    assert event.payload == {"source": "cli"}
    assert event.trigger_id.startswith("TRIG-")
    broker.publish.assert_awaited_once()


async def test_fire_on_demand_default_empty_payload() -> None:
    """fire_on_demand with no payload argument should use an empty dict."""
    broker = _make_mock_broker()
    engine = _make_engine(broker=broker)

    event = await engine.fire_on_demand(user_id="USER-B")
    assert event.payload == {}


# ---------------------------------------------------------------------------
# Scheduled loop
# ---------------------------------------------------------------------------


async def test_scheduled_loop_fires_due_triggers() -> None:
    """The scheduled loop should dispatch events for due triggers and advance them."""
    broker = _make_mock_broker()
    scheduler = TriggerScheduler()

    past = _utc(2026, 3, 17, 8, 0)
    config = TriggerConfig(
        trigger_id="TC-sched-1",
        trigger_type=TriggerType.TIME_BASED,
        user_id="USER-1",
        enabled=True,
        cron_expression="0 8 * * *",
        next_fire_at=past,
    )
    scheduler.register(config)

    engine = _make_engine(broker=broker, scheduler=scheduler)
    engine._running = True  # simulate engine started before calling loop directly

    # Patch asyncio.sleep so the loop doesn't actually wait
    sleep_call_count = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal sleep_call_count
        sleep_call_count += 1
        # Stop the loop after first sleep
        engine._running = False

    with patch("graphclaw.triggers.engine.asyncio.sleep", side_effect=fake_sleep):
        await engine._scheduled_trigger_loop()

    # Should have published exactly one event
    assert broker.publish.await_count == 1
    call_args = broker.publish.call_args
    payload = json.loads(call_args[0][1])
    assert payload["trigger_type"] == TriggerType.TIME_BASED
    assert payload["user_id"] == "USER-1"


# ---------------------------------------------------------------------------
# Event consumer loop
# ---------------------------------------------------------------------------


async def test_event_consumer_loop_processes_messages() -> None:
    """The consumer loop should convert raw messages to INBOUND trigger events."""

    async def _fake_consume(queue_name: str):
        yield "hello from email"
        yield "second message"

    broker = _make_mock_broker()
    broker.consume = _fake_consume

    engine = _make_engine(broker=broker)
    engine._running = True

    await engine._event_consumer_loop()

    # Two messages → two publish calls
    assert broker.publish.await_count == 2

    # Check the payload of the first call
    first_payload = json.loads(broker.publish.call_args_list[0][0][1])
    assert first_payload["trigger_type"] == TriggerType.INBOUND
    assert first_payload["payload"]["raw_message"] == "hello from email"


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


async def test_stop_cancels_tasks() -> None:
    """stop() should cancel background tasks without raising."""

    async def _fake_consume(queue_name: str):
        # Never yields — simulates a blocking consume
        await asyncio.sleep(9999)
        return
        yield  # make it an async generator

    broker = _make_mock_broker()
    broker.consume = _fake_consume

    with patch("graphclaw.triggers.engine.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.sleep  # delegate to real sleep
        engine = _make_engine(broker=broker)
        await engine.start()
        assert len(engine._tasks) == 2

        await engine.stop()
        # After stop, all tasks should be done
        for task in engine._tasks:
            assert task.done()
