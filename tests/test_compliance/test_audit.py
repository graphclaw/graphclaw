"""Tests for graphclaw.compliance.audit — AuditLogger."""
# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.models import AuditEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    action: str = "task.created",
    user_id: str = "USER-test",
    resource_type: str = "TaskNode",
    resource_id: str = "TSK-AB-0001-ATM",
    ts: datetime | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id="AUDIT-abc123def456",
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        timestamp=ts or datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC),
        metadata=metadata or {},
    )


def _make_storage() -> MagicMock:
    storage = MagicMock()
    storage.write = AsyncMock()
    storage.read = AsyncMock()
    storage.list_objects = AsyncMock(return_value=[])
    storage.delete = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    return storage


# ---------------------------------------------------------------------------
# test_log_writes_to_storage
# ---------------------------------------------------------------------------


async def test_log_writes_to_storage() -> None:
    storage = _make_storage()
    logger = AuditLogger(storage=storage)
    event = _make_event()

    await logger.log(event)

    storage.write.assert_awaited_once()
    call_args = storage.write.call_args
    path: str = call_args[0][0]
    # Path must follow audit/{user_id}/{YYYY-MM}/{event_id}.json convention
    assert path.startswith("audit/USER-test/2024-06/")
    assert path.endswith("AUDIT-abc123def456.json")
    # Written bytes must be valid JSON containing the event_id
    written_bytes: bytes = call_args[0][1]
    payload = json.loads(written_bytes.decode())
    assert payload["event_id"] == "AUDIT-abc123def456"
    assert payload["action"] == "task.created"


# ---------------------------------------------------------------------------
# test_scrub_sensitive_strips_sk_ant
# ---------------------------------------------------------------------------


def test_scrub_sensitive_strips_sk_ant() -> None:
    data = {"api_key": "sk-ant-api03-abc123", "name": "my-task"}
    result = AuditLogger.scrub_sensitive(data)
    assert result["api_key"] == "[REDACTED]"
    assert result["name"] == "my-task"


# ---------------------------------------------------------------------------
# test_scrub_sensitive_strips_bearer
# ---------------------------------------------------------------------------


def test_scrub_sensitive_strips_bearer() -> None:
    data = {"authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.xyz"}
    result = AuditLogger.scrub_sensitive(data)
    assert result["authorization"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# test_scrub_sensitive_nested
# ---------------------------------------------------------------------------


def test_scrub_sensitive_nested() -> None:
    data = {
        "outer": "safe_value",
        "nested": {
            "deep_key": "wg_agent_abc123",
            "safe_inner": "hello",
        },
    }
    result = AuditLogger.scrub_sensitive(data)
    assert result["outer"] == "safe_value"
    assert result["nested"]["deep_key"] == "[REDACTED]"
    assert result["nested"]["safe_inner"] == "hello"


# ---------------------------------------------------------------------------
# test_scrub_sensitive_leaves_safe_values
# ---------------------------------------------------------------------------


def test_scrub_sensitive_leaves_safe_values() -> None:
    data = {
        "user_id": "USER-abc123",
        "action": "task.created",
        "resource_type": "TaskNode",
        "count": 42,
    }
    result = AuditLogger.scrub_sensitive(data)
    assert result == data


# ---------------------------------------------------------------------------
# test_get_events_filters_by_action
# ---------------------------------------------------------------------------


async def test_get_events_filters_by_action() -> None:
    storage = _make_storage()
    logger = AuditLogger(storage=storage)

    user_id = "USER-filter-test"
    ts_login = datetime(2024, 6, 10, 9, 0, 0, tzinfo=UTC)
    ts_task = datetime(2024, 6, 12, 11, 0, 0, tzinfo=UTC)

    login_event = AuditEvent(
        event_id="AUDIT-login00001",
        user_id=user_id,
        action="auth.login",
        resource_type="UserNode",
        resource_id=user_id,
        timestamp=ts_login,
    )
    task_event = AuditEvent(
        event_id="AUDIT-task00001",
        user_id=user_id,
        action="task.created",
        resource_type="TaskNode",
        resource_id="TSK-AB-0001-ATM",
        timestamp=ts_task,
    )

    def _make_key(evt: AuditEvent) -> str:
        month = evt.timestamp.strftime("%Y-%m")
        return f"audit/{user_id}/{month}/{evt.event_id}.json"

    login_key = _make_key(login_event)
    task_key = _make_key(task_event)

    login_payload = json.dumps(
        {
            "event_id": login_event.event_id,
            "user_id": login_event.user_id,
            "action": login_event.action,
            "resource_type": login_event.resource_type,
            "resource_id": login_event.resource_id,
            "timestamp": login_event.timestamp.isoformat(),
            "ip_address": None,
            "metadata": {},
        }
    ).encode()

    task_payload = json.dumps(
        {
            "event_id": task_event.event_id,
            "user_id": task_event.user_id,
            "action": task_event.action,
            "resource_type": task_event.resource_type,
            "resource_id": task_event.resource_id,
            "timestamp": task_event.timestamp.isoformat(),
            "ip_address": None,
            "metadata": {},
        }
    ).encode()

    storage.list_objects.return_value = [login_key, task_key]
    storage.read.side_effect = AsyncMock(
        side_effect=lambda key: login_payload if key == login_key else task_payload
    )

    start = datetime(2024, 6, 1, tzinfo=UTC)
    end = datetime(2024, 7, 1, tzinfo=UTC)

    # Filter for only "auth.login" events
    events = await logger.get_events(
        user_id=user_id, start=start, end=end, action_filter="auth.login"
    )
    assert len(events) == 1
    assert events[0].action == "auth.login"
    assert events[0].event_id == "AUDIT-login00001"
