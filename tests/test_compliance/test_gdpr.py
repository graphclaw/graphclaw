# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.compliance.gdpr — GDPRService."""
# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

from datetime import timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.gdpr import GDPRService
from graphclaw.compliance.models import ErasureRequest, ErasureStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage() -> MagicMock:
    storage = MagicMock()
    storage.write = AsyncMock()
    storage.read = AsyncMock(return_value=b"{}")
    storage.list_objects = AsyncMock(return_value=[])
    storage.delete = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    return storage


def _make_graph() -> MagicMock:
    graph = MagicMock()
    graph.update_node = AsyncMock(return_value={"id": "USER-test"})
    graph.delete_node = AsyncMock()
    graph.list_nodes = AsyncMock(return_value=[])
    graph.get_node = AsyncMock(return_value={"id": "USER-test"})
    graph.create_node = AsyncMock(return_value={})
    graph.create_edge = AsyncMock(return_value={})
    graph.get_edges = AsyncMock(return_value=[])
    graph.delete_edge = AsyncMock()
    return graph


def _make_service() -> tuple[GDPRService, MagicMock, MagicMock]:
    storage = _make_storage()
    graph = _make_graph()
    audit = AuditLogger(storage=storage)
    service = GDPRService(graph_store=graph, storage=storage, audit_logger=audit)
    return service, graph, storage


# ---------------------------------------------------------------------------
# test_request_erasure_creates_request
# ---------------------------------------------------------------------------


async def test_request_erasure_creates_request() -> None:
    service, _, _ = _make_service()
    request = await service.request_erasure(
        user_id="USER-abc",
        requester_email="alice@example.com",
        reason="Leaving the platform",
    )
    assert isinstance(request, ErasureRequest)
    assert request.user_id == "USER-abc"
    assert request.requester_email == "alice@example.com"
    assert request.reason == "Leaving the platform"
    assert request.request_id.startswith("ERASURE-")


# ---------------------------------------------------------------------------
# test_process_erasure_anonymizes_user
# ---------------------------------------------------------------------------


async def test_process_erasure_anonymizes_user() -> None:
    service, graph, _ = _make_service()
    request = await service.request_erasure(user_id="USER-xyz", requester_email="bob@example.com")
    await service.process_erasure(request)

    graph.update_node.assert_awaited_once()
    call_args = graph.update_node.call_args
    node_id: str = call_args[0][0]
    updates: dict = call_args[0][1]

    assert node_id == "USER-xyz"
    assert updates["name"] == "[deleted]"
    assert "USER-xyz" in updates["email"]
    assert "anon.graphclaw.ai" in updates["email"]
    assert updates["phone"] is None


# ---------------------------------------------------------------------------
# test_process_erasure_deletes_tasks
# ---------------------------------------------------------------------------


async def test_process_erasure_deletes_tasks() -> None:
    service, graph, _ = _make_service()

    task_records = [
        {"id": "TSK-AB-0001-ATM"},
        {"id": "TSK-AB-0002-DEL"},
    ]
    graph.list_nodes = AsyncMock(return_value=task_records)

    request = await service.request_erasure(
        user_id="USER-del", requester_email="charlie@example.com"
    )
    await service.process_erasure(request)

    # delete_node should have been called for each task
    deleted_ids = {call.args[0] for call in graph.delete_node.call_args_list}
    assert "TSK-AB-0001-ATM" in deleted_ids
    assert "TSK-AB-0002-DEL" in deleted_ids


# ---------------------------------------------------------------------------
# test_process_erasure_returns_completed
# ---------------------------------------------------------------------------


async def test_process_erasure_returns_completed() -> None:
    service, _, _ = _make_service()
    request = await service.request_erasure(user_id="USER-ok", requester_email="ok@example.com")
    result = await service.process_erasure(request)
    assert result == ErasureStatus.COMPLETED


# ---------------------------------------------------------------------------
# test_process_erasure_returns_failed_on_error
# ---------------------------------------------------------------------------


async def test_process_erasure_returns_failed_on_error() -> None:
    service, graph, _ = _make_service()
    # Make update_node raise to simulate a graph failure
    graph.update_node = AsyncMock(side_effect=RuntimeError("DB connection lost"))

    request = await service.request_erasure(user_id="USER-fail", requester_email="fail@example.com")
    result = await service.process_erasure(request)
    assert result == ErasureStatus.FAILED


# ---------------------------------------------------------------------------
# test_erasure_request_frozen
# ---------------------------------------------------------------------------


def test_erasure_request_frozen() -> None:
    from datetime import datetime

    request = ErasureRequest(
        user_id="USER-frozen",
        requested_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        requester_email="frozen@example.com",
        request_id="ERASURE-aabbccddee",
    )
    with pytest.raises((AttributeError, TypeError)):
        request.user_id = "USER-mutated"  # type: ignore[misc]
