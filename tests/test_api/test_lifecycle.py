# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_lifecycle — Unit tests for admin lifecycle endpoints.

Tests cover:
- cancel_purge: rejects if no pending purge, clears fields, writes audit.
- confirm_purge: rejects if no pending purge / legal hold, moves purge_after.
- right_to_erasure: rejects stale re-auth, rejects legal hold, sets immediate purge.
- set_legal_hold: rejects if already held, sets fields, writes audit.
- release_legal_hold: rejects if not held, clears fields, writes audit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from graphclaw.api.admin.lifecycle import (
    CancelPurgeRequest,
    ConfirmPurgeRequest,
    LegalHoldRequest,
    RightToErasureRequest,
    cancel_purge,
    confirm_purge,
    release_legal_hold,
    right_to_erasure,
    set_legal_hold,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_node(purge_after=None, legal_hold=False, purge_cancelled_at=None):
    node = MagicMock()
    node.id = "USER-test"
    node.purge_after = purge_after
    node.legal_hold = legal_hold
    node.purge_cancelled_at = purge_cancelled_at
    return node


def _make_store(node=None):
    store = MagicMock()
    store.get_node = AsyncMock(return_value=node)
    store.update_node = AsyncMock()
    return store


def _make_storage():
    storage = MagicMock()
    storage.read = AsyncMock(side_effect=FileNotFoundError)
    storage.write = AsyncMock()
    return storage


# ---------------------------------------------------------------------------
# cancel_purge tests
# ---------------------------------------------------------------------------


class TestCancelPurge:
    async def test_raises_404_if_node_missing(self) -> None:
        store = _make_store(node=None)
        with pytest.raises(HTTPException) as exc:
            await cancel_purge(
                CancelPurgeRequest(user_id="USER-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 404

    async def test_raises_409_if_no_pending_purge(self) -> None:
        store = _make_store(_make_node(purge_after=None))
        with pytest.raises(HTTPException) as exc:
            await cancel_purge(
                CancelPurgeRequest(user_id="USER-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 409

    async def test_clears_purge_fields(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future))
        await cancel_purge(CancelPurgeRequest(user_id="USER-x"), "admin", store, _make_storage())
        store.update_node.assert_called_once()
        updates = store.update_node.call_args[0][1]
        assert updates["purge_after"] is None
        assert updates["archived_at"] is None
        assert updates["purge_cancelled_at"] is not None

    async def test_writes_audit_entry(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future))
        storage = _make_storage()
        await cancel_purge(CancelPurgeRequest(user_id="USER-x"), "admin", store, storage)
        storage.write.assert_called_once()
        written_data = storage.write.call_args[0][1].decode()
        assert "purge_cancelled" in written_data

    async def test_returns_ok(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future))
        result = await cancel_purge(
            CancelPurgeRequest(user_id="USER-x"), "admin", store, _make_storage()
        )
        assert result.ok is True


# ---------------------------------------------------------------------------
# confirm_purge tests
# ---------------------------------------------------------------------------


class TestConfirmPurge:
    async def test_raises_409_if_no_pending_purge(self) -> None:
        store = _make_store(_make_node(purge_after=None))
        with pytest.raises(HTTPException) as exc:
            await confirm_purge(
                ConfirmPurgeRequest(user_id="USER-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 409

    async def test_raises_409_if_legal_hold(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future, legal_hold=True))
        with pytest.raises(HTTPException) as exc:
            await confirm_purge(
                ConfirmPurgeRequest(user_id="USER-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 409

    async def test_moves_purge_after_to_past(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future))
        await confirm_purge(ConfirmPurgeRequest(user_id="USER-x"), "admin", store, _make_storage())
        updates = store.update_node.call_args[0][1]
        assert updates["purge_after"] < datetime.now(UTC)

    async def test_writes_audit_entry(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=20)
        store = _make_store(_make_node(purge_after=future))
        storage = _make_storage()
        await confirm_purge(ConfirmPurgeRequest(user_id="USER-x"), "admin", store, storage)
        storage.write.assert_called_once()
        assert "purge_confirmed" in storage.write.call_args[0][1].decode()


# ---------------------------------------------------------------------------
# right_to_erasure tests
# ---------------------------------------------------------------------------


class TestRightToErasure:
    def _recent_reauth(self) -> datetime:
        return datetime.now(UTC) - timedelta(minutes=1)

    def _stale_reauth(self) -> datetime:
        return datetime.now(UTC) - timedelta(minutes=10)

    async def test_raises_403_if_reauth_stale(self) -> None:
        store = _make_store(_make_node())
        with pytest.raises(HTTPException) as exc:
            await right_to_erasure(
                RightToErasureRequest(
                    subject_id="USER-x",
                    justification="test",
                    re_auth_at=self._stale_reauth(),
                ),
                "admin",
                store,
                _make_storage(),
            )
        assert exc.value.status_code == 403

    async def test_raises_409_if_legal_hold(self) -> None:
        store = _make_store(_make_node(legal_hold=True))
        with pytest.raises(HTTPException) as exc:
            await right_to_erasure(
                RightToErasureRequest(
                    subject_id="USER-x",
                    justification="test",
                    re_auth_at=self._recent_reauth(),
                ),
                "admin",
                store,
                _make_storage(),
            )
        assert exc.value.status_code == 409

    async def test_raises_404_if_node_missing(self) -> None:
        store = _make_store(node=None)
        with pytest.raises(HTTPException) as exc:
            await right_to_erasure(
                RightToErasureRequest(
                    subject_id="USER-x",
                    justification="test",
                    re_auth_at=self._recent_reauth(),
                ),
                "admin",
                store,
                _make_storage(),
            )
        assert exc.value.status_code == 404

    async def test_marks_node_for_immediate_purge(self) -> None:
        store = _make_store(_make_node())
        await right_to_erasure(
            RightToErasureRequest(
                subject_id="USER-x",
                justification="GDPR request",
                re_auth_at=self._recent_reauth(),
            ),
            "admin",
            store,
            _make_storage(),
        )
        updates = store.update_node.call_args[0][1]
        assert updates["archived_at"] is not None
        assert updates["purge_after"] < datetime.now(UTC)

    async def test_writes_two_audit_entries(self) -> None:
        store = _make_store(_make_node())
        storage = _make_storage()
        # Second write needs to read first entry
        first_write: list[bytes] = []

        async def capture_write(path: str, data: bytes, **kwargs: object) -> None:
            first_write.append(data)
            storage.read.side_effect = None
            storage.read.return_value = data

        storage.write.side_effect = capture_write
        result = await right_to_erasure(
            RightToErasureRequest(
                subject_id="USER-x",
                justification="GDPR",
                re_auth_at=self._recent_reauth(),
            ),
            "admin",
            store,
            storage,
        )
        assert storage.write.call_count == 2
        assert result.audit_entry_id


# ---------------------------------------------------------------------------
# set_legal_hold tests
# ---------------------------------------------------------------------------


class TestSetLegalHold:
    async def test_raises_404_if_missing(self) -> None:
        store = _make_store(None)
        with pytest.raises(HTTPException) as exc:
            await set_legal_hold(
                "TASK-x", LegalHoldRequest(reason="litigation"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 404

    async def test_raises_409_if_already_held(self) -> None:
        store = _make_store(_make_node(legal_hold=True))
        with pytest.raises(HTTPException) as exc:
            await set_legal_hold("TASK-x", LegalHoldRequest(), "admin", store, _make_storage())
        assert exc.value.status_code == 409

    async def test_sets_legal_hold_fields(self) -> None:
        store = _make_store(_make_node(legal_hold=False))
        await set_legal_hold(
            "TASK-x", LegalHoldRequest(reason="litigation"), "admin", store, _make_storage()
        )
        updates = store.update_node.call_args[0][1]
        assert updates["legal_hold"] is True
        assert updates["hold_set_by"] == "admin"

    async def test_returns_legal_hold_true(self) -> None:
        store = _make_store(_make_node(legal_hold=False))
        result = await set_legal_hold("TASK-x", LegalHoldRequest(), "admin", store, _make_storage())
        assert result.legal_hold is True


# ---------------------------------------------------------------------------
# release_legal_hold tests
# ---------------------------------------------------------------------------


class TestReleaseLegalHold:
    async def test_raises_409_if_not_held(self) -> None:
        store = _make_store(_make_node(legal_hold=False))
        with pytest.raises(HTTPException) as exc:
            await release_legal_hold("TASK-x", "admin", store, _make_storage())
        assert exc.value.status_code == 409

    async def test_clears_legal_hold_fields(self) -> None:
        store = _make_store(_make_node(legal_hold=True))
        await release_legal_hold("TASK-x", "admin", store, _make_storage())
        updates = store.update_node.call_args[0][1]
        assert updates["legal_hold"] is False
        assert updates["hold_reason"] is None

    async def test_returns_legal_hold_false(self) -> None:
        store = _make_store(_make_node(legal_hold=True))
        result = await release_legal_hold("TASK-x", "admin", store, _make_storage())
        assert result.legal_hold is False

    async def test_writes_audit_entry(self) -> None:
        store = _make_store(_make_node(legal_hold=True))
        storage = _make_storage()
        await release_legal_hold("TASK-x", "admin", store, storage)
        storage.write.assert_called_once()
        assert "legal_hold_released" in storage.write.call_args[0][1].decode()
