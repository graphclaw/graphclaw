# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_auth.test_purge_gate — Unit tests for pending-purge gate (FR-DEL-004).

Tests cover:
- _check_pending_purge_gate passes when no purge_after.
- _check_pending_purge_gate raises 423 when purge_after is set and not cancelled.
- _check_pending_purge_gate passes when purge_cancelled_at is set (cancellation took effect).
- 423 response body has correct purge_after and purge_initiated_at.
- No-op when graph_store is None on app.state.
- No-op when node is None.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from graphclaw.auth.routes import _check_pending_purge_gate


def _make_request(graph_store=None):
    request = MagicMock()
    request.app.state.graph_store = graph_store
    return request


def _make_node(purge_after=None, purge_cancelled_at=None, archived_at=None):
    node = MagicMock()
    node.purge_after = purge_after
    node.purge_cancelled_at = purge_cancelled_at
    node.archived_at = archived_at
    return node


def _make_store(node=None):
    store = MagicMock()
    store.get_node = AsyncMock(return_value=node)
    return store


class TestCheckPendingPurgeGate:
    async def test_passes_when_no_graph_store(self) -> None:
        request = _make_request(graph_store=None)
        # Should not raise
        await _check_pending_purge_gate(request, "USER-x")

    async def test_passes_when_node_is_none(self) -> None:
        store = _make_store(node=None)
        request = _make_request(store)
        await _check_pending_purge_gate(request, "USER-x")

    async def test_passes_when_no_purge_after(self) -> None:
        node = _make_node(purge_after=None)
        store = _make_store(node)
        request = _make_request(store)
        await _check_pending_purge_gate(request, "USER-x")

    async def test_passes_when_purge_cancelled(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        cancelled = datetime.now(UTC) - timedelta(minutes=5)
        node = _make_node(purge_after=future, purge_cancelled_at=cancelled)
        store = _make_store(node)
        request = _make_request(store)
        # purge_cancelled_at is set → should not raise
        await _check_pending_purge_gate(request, "USER-x")

    async def test_raises_423_when_pending_purge(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        now = datetime.now(UTC)
        node = _make_node(purge_after=future, purge_cancelled_at=None, archived_at=now)
        store = _make_store(node)
        request = _make_request(store)
        with pytest.raises(HTTPException) as exc:
            await _check_pending_purge_gate(request, "USER-x")
        assert exc.value.status_code == 423

    async def test_423_body_has_purge_after(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        archived = datetime.now(UTC) - timedelta(minutes=30)
        node = _make_node(purge_after=future, purge_cancelled_at=None, archived_at=archived)
        store = _make_store(node)
        request = _make_request(store)
        with pytest.raises(HTTPException) as exc:
            await _check_pending_purge_gate(request, "USER-x")
        detail = exc.value.detail
        assert detail["code"] == "PENDING_PURGE"
        assert "purge_after" in detail
        assert "purge_initiated_at" in detail

    async def test_passes_when_store_raises(self) -> None:
        """Non-fatal: if graph store is unreachable, let login through."""
        store = MagicMock()
        store.get_node = AsyncMock(side_effect=RuntimeError("DB down"))
        request = _make_request(store)
        # Should NOT raise — non-fatal degradation
        await _check_pending_purge_gate(request, "USER-x")
