# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.compliance.export — DataExportService."""
# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.export import DataExportService
from graphclaw.compliance.models import DataExport

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


def _make_graph(user_node: dict | None = None, tasks: list | None = None) -> MagicMock:
    graph = MagicMock()
    graph.get_node = AsyncMock(return_value=user_node or {"id": "USER-test", "name": "Test User"})
    graph.list_nodes = AsyncMock(return_value=tasks or [])
    graph.create_node = AsyncMock(return_value={})
    graph.update_node = AsyncMock(return_value={})
    graph.delete_node = AsyncMock()
    graph.create_edge = AsyncMock(return_value={})
    graph.get_edges = AsyncMock(return_value=[])
    graph.delete_edge = AsyncMock()
    return graph


def _make_service(
    graph: MagicMock | None = None,
    storage: MagicMock | None = None,
) -> tuple[DataExportService, MagicMock, MagicMock]:
    s = storage or _make_storage()
    g = graph or _make_graph()
    audit = AuditLogger(storage=s)
    service = DataExportService(graph_store=g, storage=s, audit_logger=audit)
    return service, g, s


# ---------------------------------------------------------------------------
# test_export_user_data_writes_json
# ---------------------------------------------------------------------------


async def test_export_user_data_writes_json() -> None:
    service, _, storage = _make_service()
    export = await service.export_user_data("USER-test")

    # The service writes two objects: the export JSON and the audit event.
    # At minimum, write must have been called.
    assert storage.write.await_count >= 1

    # Find the call that wrote the export archive (not an audit event)
    export_write_call = None
    for call in storage.write.call_args_list:
        path: str = call[0][0]
        if path.startswith("exports/"):
            export_write_call = call
            break

    assert export_write_call is not None, "Expected an export write call"
    path = export_write_call[0][0]
    assert path == f"exports/USER-test/{export.export_id}/data.json"

    # Written bytes must be valid JSON
    written_bytes: bytes = export_write_call[0][1]
    payload = json.loads(written_bytes.decode())
    assert payload["user_id"] == "USER-test"
    assert payload["export_id"] == export.export_id


# ---------------------------------------------------------------------------
# test_export_contains_user_node
# ---------------------------------------------------------------------------


async def test_export_contains_user_node() -> None:
    user_node_data = {"id": "USER-test", "name": "Alice", "email": "alice@example.com"}
    graph = _make_graph(user_node=user_node_data)
    service, _, storage = _make_service(graph=graph)

    await service.export_user_data("USER-test")

    # Find the export archive write call
    export_write_call = None
    for call in storage.write.call_args_list:
        path: str = call[0][0]
        if path.startswith("exports/"):
            export_write_call = call
            break

    assert export_write_call is not None
    written_bytes: bytes = export_write_call[0][1]
    payload = json.loads(written_bytes.decode())
    assert payload["user_node"] is not None
    assert payload["user_node"]["name"] == "Alice"


# ---------------------------------------------------------------------------
# test_export_expires_in_7_days
# ---------------------------------------------------------------------------


async def test_export_expires_in_7_days() -> None:
    service, _, _ = _make_service()
    export = await service.export_user_data("USER-ttl")

    delta = export.expires_at - export.created_at
    assert delta == timedelta(days=7)


# ---------------------------------------------------------------------------
# test_data_export_frozen
# ---------------------------------------------------------------------------


def test_data_export_frozen() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    export = DataExport(
        user_id="USER-frozen",
        export_id="EXPORT-aabbccddee",
        created_at=now,
        storage_key="exports/USER-frozen/EXPORT-aabbccddee/data.json",
        expires_at=now + timedelta(days=7),
        record_count=5,
    )
    with pytest.raises((AttributeError, TypeError)):
        export.user_id = "USER-mutated"  # type: ignore[misc]
