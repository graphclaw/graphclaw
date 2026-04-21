"""tests.test_agent.test_event_consumer — Unit tests for AgentEventConsumer.

Description
-----------
Verifies the AgentEventConsumer event routing, briefing dispatch, inbound
trigger handling, event-based scoring, inbox writing, unmatched notification,
and lifecycle (start/stop/register_user_channels).  All dependencies (broker,
agent_loop, dispatcher, storage) are mocked so no external services are needed.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, calls the handler directly,
  and asserts on mock calls or returned state.
- Direct Handler Testing: Rather than driving the background consume loops (which
  run forever), tests call internal handler methods (_handle_event,
  _handle_briefing_trigger, etc.) directly to verify routing logic.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.agent.event_consumer: AgentEventConsumer.
- graphclaw.triggers.models: TriggerEvent, TriggerType.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from graphclaw.agent.event_consumer import AgentEventConsumer
from graphclaw.triggers.models import TriggerEvent, TriggerType

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_consumer(
    user_channels: dict | None = None,
    default_user_id: str = "usr-001",
    storage: object | None = None,
) -> tuple[AgentEventConsumer, AsyncMock, AsyncMock, AsyncMock]:
    """Return an AgentEventConsumer with all dependencies mocked."""
    mock_broker = AsyncMock()

    # consume() returns an async generator — use AsyncMock with __aiter__
    async def _empty_gen(*_a, **_kw):
        return
        yield  # make it an async generator  # noqa: RET901

    mock_broker.consume = MagicMock(side_effect=_empty_gen)

    mock_loop = AsyncMock()
    mock_loop._llm = None  # No LLM so intelligence_agent is not wired
    mock_loop.run_cycle = AsyncMock(return_value=[])
    mock_loop.generate_briefing = AsyncMock(return_value="Your daily briefing")
    mock_loop.process_chat_message = AsyncMock(return_value=None)

    mock_dispatcher = AsyncMock()
    mock_dispatcher.broadcast = AsyncMock()
    mock_dispatcher.send_email = AsyncMock()
    mock_dispatcher.send_telegram = AsyncMock()

    consumer = AgentEventConsumer(
        broker=mock_broker,
        agent_loop=mock_loop,
        dispatcher=mock_dispatcher,
        user_channels=user_channels,
        default_user_id=default_user_id,
        storage=storage,
    )
    return consumer, mock_broker, mock_loop, mock_dispatcher


def _make_trigger(
    trigger_type: TriggerType,
    user_id: str = "usr-001",
    payload: dict | None = None,
) -> TriggerEvent:
    return TriggerEvent(
        trigger_id="trig-001",
        trigger_type=trigger_type,
        user_id=user_id,
        payload=payload or {},
    )


def _make_inbound_dict(
    channel: str = "email",
    sender: str = "sender@example.com",
    subject: str = "Test subject",
    body: str = "Test body content",
    message_id: str = "msg-001",
) -> dict:
    return {
        "message_id": message_id,
        "channel": channel,
        "sender": sender,
        "subject": subject,
        "body": body,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "session_id": "SES-test-001",
    }


# ---------------------------------------------------------------------------
# register_user_channels
# ---------------------------------------------------------------------------


def test_register_user_channels_stores_channels() -> None:
    """register_user_channels() stores channel list for user."""
    consumer, _, _, _ = _make_consumer()
    channels = [{"channel": "email", "to": "user@example.com"}]
    consumer.register_user_channels("usr-001", channels)
    assert consumer._user_channels["usr-001"] == channels


def test_register_user_channels_overwrites_existing() -> None:
    """Calling register_user_channels() twice replaces the previous entry."""
    consumer, _, _, _ = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "old@x.com"}]}
    )
    new_channels = [{"channel": "telegram", "to": "123456"}]
    consumer.register_user_channels("usr-001", new_channels)
    assert consumer._user_channels["usr-001"] == new_channels


# ---------------------------------------------------------------------------
# _handle_event — routing
# ---------------------------------------------------------------------------


async def test_handle_event_routes_time_based_to_briefing() -> None:
    """TIME_BASED → _handle_briefing_trigger is called."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    event = _make_trigger(TriggerType.TIME_BASED)
    await consumer._handle_event(event)
    mock_loop.run_cycle.assert_called_once_with(user_id="usr-001", trigger_source="heartbeat")
    mock_dispatcher.broadcast.assert_called_once()


async def test_handle_event_routes_on_demand_to_briefing() -> None:
    """ON_DEMAND → _handle_briefing_trigger is called."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    event = _make_trigger(TriggerType.ON_DEMAND)
    await consumer._handle_event(event)
    mock_loop.run_cycle.assert_called_once_with(user_id="usr-001", trigger_source="on_demand")


async def test_handle_event_routes_event_based_to_scoring() -> None:
    """EVENT_BASED → run_cycle is called but briefing is NOT dispatched."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    event = _make_trigger(TriggerType.EVENT_BASED)
    await consumer._handle_event(event)
    mock_loop.run_cycle.assert_called_once_with(
        user_id="usr-001", trigger_source="property_change"
    )
    mock_dispatcher.broadcast.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_briefing_trigger
# ---------------------------------------------------------------------------


async def test_briefing_trigger_dispatches_to_registered_channels() -> None:
    """Briefing is sent to user's registered channels."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    event = _make_trigger(TriggerType.TIME_BASED)
    await consumer._handle_briefing_trigger(event)

    mock_loop.run_cycle.assert_called_once_with(user_id="usr-001", trigger_source="heartbeat")
    mock_loop.generate_briefing.assert_called_once()
    mock_dispatcher.broadcast.assert_called_once()
    call_kwargs = mock_dispatcher.broadcast.call_args[1]
    assert call_kwargs["subject"] == "Your GraphClaw Briefing"
    assert "daily briefing" in call_kwargs["body"]


async def test_briefing_trigger_skips_when_no_channels() -> None:
    """Briefing is not dispatched when user has no registered channels."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(user_channels={})
    event = _make_trigger(TriggerType.TIME_BASED)
    await consumer._handle_briefing_trigger(event)

    mock_dispatcher.broadcast.assert_not_called()


async def test_briefing_trigger_skips_when_no_user_id() -> None:
    """Briefing trigger with empty user_id is silently skipped."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer()
    event = _make_trigger(TriggerType.TIME_BASED, user_id="")
    await consumer._handle_briefing_trigger(event)

    mock_loop.run_cycle.assert_not_called()
    mock_dispatcher.broadcast.assert_not_called()


async def test_briefing_trigger_run_cycle_exception_does_not_propagate() -> None:
    """Exception in run_cycle is caught; consumer does not crash."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    mock_loop.run_cycle = AsyncMock(side_effect=RuntimeError("scoring failed"))
    event = _make_trigger(TriggerType.TIME_BASED)
    # Should not raise
    await consumer._handle_briefing_trigger(event)
    mock_dispatcher.broadcast.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_event_based_trigger
# ---------------------------------------------------------------------------


async def test_event_based_trigger_runs_scoring_cycle() -> None:
    """EVENT_BASED trigger calls run_cycle once."""
    consumer, _, mock_loop, _ = _make_consumer()
    event = _make_trigger(TriggerType.EVENT_BASED)
    await consumer._handle_event_based_trigger(event)
    mock_loop.run_cycle.assert_called_once_with(
        user_id="usr-001", trigger_source="property_change"
    )


async def test_event_based_trigger_cycle_exception_does_not_propagate() -> None:
    """Exception in run_cycle is caught for event-based trigger."""
    consumer, _, mock_loop, _ = _make_consumer()
    mock_loop.run_cycle = AsyncMock(side_effect=RuntimeError("error"))
    event = _make_trigger(TriggerType.EVENT_BASED)
    await consumer._handle_event_based_trigger(event)  # Should not raise


# ---------------------------------------------------------------------------
# _handle_inbound_trigger
# ---------------------------------------------------------------------------


async def test_inbound_trigger_skips_empty_payload() -> None:
    """INBOUND trigger with no raw_message in payload does nothing."""
    consumer, _, mock_loop, mock_dispatcher = _make_consumer()
    event = _make_trigger(TriggerType.INBOUND, payload={})
    # Should not crash or call any collaborators
    await consumer._handle_inbound_trigger(event)
    mock_loop.process_chat_message.assert_not_called()


async def test_inbound_trigger_deserializes_and_processes() -> None:
    """INBOUND trigger with valid raw_message calls _process_raw_inbound."""
    consumer, _, _, _ = _make_consumer()
    raw = json.dumps(_make_inbound_dict())
    event = _make_trigger(TriggerType.INBOUND, payload={"raw_message": raw})

    # Patch _process_raw_inbound to avoid full InboundProcessor setup
    consumer._process_raw_inbound = AsyncMock()
    await consumer._handle_inbound_trigger(event)
    consumer._process_raw_inbound.assert_called_once()


async def test_inbound_trigger_invalid_json_does_not_crash() -> None:
    """Malformed JSON in raw_message is caught; consumer does not crash."""
    consumer, _, _, _ = _make_consumer()
    event = _make_trigger(TriggerType.INBOUND, payload={"raw_message": "{not valid json"})
    await consumer._handle_inbound_trigger(event)  # Should not raise


# ---------------------------------------------------------------------------
# _write_inbox_entries
# ---------------------------------------------------------------------------


async def test_write_inbox_entries_writes_archive_and_recent() -> None:
    """_write_inbox_entries() writes two files: archive and recent."""
    from graphclaw.gateway.schemas import InboundMessage
    from graphclaw.inbound.models import InboundResult, StatusExtraction, TaskResolution
    from graphclaw.models.enums import ConfidenceLevel, MatchedBy

    mock_storage = AsyncMock()
    mock_storage.write = AsyncMock()
    consumer, _, _, _ = _make_consumer(storage=mock_storage)

    inbound = InboundMessage(**_make_inbound_dict())
    resolution = TaskResolution(
        task_id="TSK-AB-0001-ATM",
        matched_by=MatchedBy.TASK_ID,
        confidence=ConfidenceLevel.HIGH,
        score=1.0,
        matched_text="TSK-AB-0001-ATM",
    )
    result = InboundResult(
        message_id="msg-001",
        session_id="SES-test",
        resolution=resolution,
        status=StatusExtraction(signal="DONE"),
    )

    await consumer._write_inbox_entries(inbound, result, "usr-001", "main")

    assert mock_storage.write.call_count == 2


async def test_write_inbox_entries_skips_when_no_storage() -> None:
    """_write_inbox_entries() is safe to call when storage is None."""
    from graphclaw.gateway.schemas import InboundMessage

    consumer, _, _, _ = _make_consumer(storage=None)
    inbound = InboundMessage(**_make_inbound_dict())

    # Should not raise
    await consumer._write_inbox_entries(inbound, None, "usr-001", "main")


async def test_write_inbox_entries_recent_has_body_summary() -> None:
    """Recent entry contains body_summary (first 150 chars)."""
    from graphclaw.gateway.schemas import InboundMessage
    from graphclaw.inbound.models import InboundResult, StatusExtraction, TaskResolution

    mock_storage = AsyncMock()
    written_data = []

    async def _capture_write(path, data, **_kw):
        written_data.append((path, json.loads(data.decode())))

    mock_storage.write = AsyncMock(side_effect=_capture_write)
    consumer, _, _, _ = _make_consumer(storage=mock_storage)

    long_body = "A" * 300
    inbound = InboundMessage(**_make_inbound_dict(body=long_body))
    resolution = TaskResolution()
    result = InboundResult(
        message_id="msg-001",
        session_id="SES-test",
        resolution=resolution,
        status=StatusExtraction(signal="UNKNOWN"),
    )

    await consumer._write_inbox_entries(inbound, result, "usr-001", "main")

    # Identify the recent entry (path contains "recent")
    recent_entries = [(p, d) for p, d in written_data if "recent" in p]
    assert len(recent_entries) == 1
    _, recent_data = recent_entries[0]
    assert len(recent_data["body_summary"]) == 150


# ---------------------------------------------------------------------------
# _notify_user_unmatched
# ---------------------------------------------------------------------------


async def test_notify_user_unmatched_broadcasts_when_channels_registered() -> None:
    """An unmatched inbound from a known sender triggers a broadcast to user channels."""
    from graphclaw.gateway.schemas import InboundMessage

    consumer, _, _, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    inbound = InboundMessage(**_make_inbound_dict(body="Hi, what is the status?"))
    await consumer._notify_user_unmatched(inbound, "usr-001")

    mock_dispatcher.broadcast.assert_called_once()
    call_kwargs = mock_dispatcher.broadcast.call_args[1]
    assert "Unmatched message" in call_kwargs["subject"]


async def test_notify_user_unmatched_skips_empty_body() -> None:
    """No broadcast when inbound body is empty."""
    from graphclaw.gateway.schemas import InboundMessage

    consumer, _, _, mock_dispatcher = _make_consumer(
        user_channels={"usr-001": [{"channel": "email", "to": "u@x.com"}]}
    )
    inbound = InboundMessage(**_make_inbound_dict(body=""))
    await consumer._notify_user_unmatched(inbound, "usr-001")

    mock_dispatcher.broadcast.assert_not_called()


async def test_notify_user_unmatched_skips_when_no_channels() -> None:
    """No broadcast when user has no registered channels."""
    from graphclaw.gateway.schemas import InboundMessage

    consumer, _, _, mock_dispatcher = _make_consumer(user_channels={})
    inbound = InboundMessage(**_make_inbound_dict(body="Hey"))
    await consumer._notify_user_unmatched(inbound, "usr-001")

    mock_dispatcher.broadcast.assert_not_called()
