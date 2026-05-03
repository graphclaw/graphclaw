"""tests.test_audit.test_immutable_log — Unit tests for AuditLog + AuditEntry.

Tests cover:
- AuditEntry.create() generates consistent hash + chaining.
- AuditLog.record() appends entries to storage.
- AuditLog.read_day() deserialises existing entries.
- Chain tamper detection (entry_hash changes if content changes).
- Legal-hold, purge, and right-to-erasure event types are valid.
- Empty log returns empty list.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.audit.immutable_log import AuditEntry, AuditEventType, AuditLog


# ---------------------------------------------------------------------------
# AuditEntry unit tests
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def test_create_generates_entry_id(self) -> None:
        entry = AuditEntry.create(
            AuditEventType.PURGE_REQUESTED,
            actor_id="USER-abc123",
            subject_id="USER-abc123",
        )
        assert entry.entry_id  # non-empty
        assert "USER-abc" in entry.entry_id

    def test_create_generates_sha256_hash(self) -> None:
        entry = AuditEntry.create(
            AuditEventType.PURGE_REQUESTED,
            actor_id="USER-x",
            subject_id="USER-x",
        )
        assert len(entry.entry_hash) == 64  # 256-bit hex

    def test_create_different_entries_have_different_hashes(self) -> None:
        e1 = AuditEntry.create(AuditEventType.PURGE_REQUESTED, "USER-a", "TASK-1")
        e2 = AuditEntry.create(AuditEventType.PURGE_CANCELLED, "USER-a", "TASK-1")
        assert e1.entry_hash != e2.entry_hash

    def test_create_chains_prev_hash(self) -> None:
        e1 = AuditEntry.create(AuditEventType.PURGE_REQUESTED, "USER-a", "TASK-1")
        e2 = AuditEntry.create(
            AuditEventType.PURGE_CONFIRMED,
            "USER-a",
            "TASK-1",
            prev_hash=e1.entry_hash,
        )
        assert e2.prev_hash == e1.entry_hash

    def test_first_entry_has_no_prev_hash(self) -> None:
        e = AuditEntry.create(AuditEventType.LEGAL_HOLD_SET, "admin", "TASK-1")
        assert e.prev_hash is None

    def test_metadata_stored(self) -> None:
        meta = {"justification": "GDPR request", "ip": "1.2.3.4"}
        e = AuditEntry.create(
            AuditEventType.RIGHT_TO_ERASURE_REQUESTED, "USER-a", "USER-a", metadata=meta
        )
        assert e.metadata["justification"] == "GDPR request"

    def test_event_type_enum_values(self) -> None:
        assert AuditEventType.PURGE_REQUESTED == "purge_requested"
        assert AuditEventType.LEGAL_HOLD_SET == "legal_hold_set"
        assert AuditEventType.RIGHT_TO_ERASURE_EXECUTED == "right_to_erasure_executed"
        assert AuditEventType.ORG_ARCHIVED == "org_archived"

    def test_roundtrip_json(self) -> None:
        e = AuditEntry.create(AuditEventType.PURGE_CANCELLED, "USER-z", "USER-z")
        restored = AuditEntry.model_validate_json(e.model_dump_json())
        assert restored.entry_hash == e.entry_hash
        assert restored.event_type == e.event_type


# ---------------------------------------------------------------------------
# AuditLog unit tests (mocked storage)
# ---------------------------------------------------------------------------


def _make_storage(existing_content: str = "") -> MagicMock:
    """Return a mock StorageClient pre-loaded with *existing_content*."""
    storage = MagicMock()
    if existing_content:
        storage.read = AsyncMock(return_value=existing_content.encode("utf-8"))
    else:
        storage.read = AsyncMock(side_effect=FileNotFoundError)
    storage.write = AsyncMock()
    return storage


class TestAuditLog:
    async def test_record_writes_entry(self) -> None:
        storage = _make_storage()
        log = AuditLog(storage)
        entry = await log.record(
            AuditEventType.PURGE_REQUESTED,
            actor_id="USER-alpha",
            subject_id="USER-alpha",
        )
        storage.write.assert_called_once()
        path_arg, data_arg, *_ = storage.write.call_args[0]
        assert "audit" in path_arg
        assert entry.entry_id.encode() in data_arg

    async def test_record_returns_audit_entry(self) -> None:
        storage = _make_storage()
        log = AuditLog(storage)
        entry = await log.record(
            AuditEventType.LEGAL_HOLD_SET, "admin", "TASK-99"
        )
        assert isinstance(entry, AuditEntry)
        assert entry.event_type == AuditEventType.LEGAL_HOLD_SET

    async def test_read_day_empty(self) -> None:
        storage = _make_storage()
        log = AuditLog(storage)
        result = await log.read_day(date(2026, 1, 1))
        assert result == []

    async def test_read_day_parses_entries(self) -> None:
        e = AuditEntry.create(AuditEventType.PURGE_CONFIRMED, "admin", "USER-x")
        storage = _make_storage(e.model_dump_json() + "\n")
        log = AuditLog(storage)
        result = await log.read_day()
        assert len(result) == 1
        assert result[0].entry_hash == e.entry_hash

    async def test_read_day_skips_malformed_lines(self) -> None:
        e = AuditEntry.create(AuditEventType.PURGE_EXECUTED, "admin", "USER-y")
        bad_content = "NOT_JSON\n" + e.model_dump_json() + "\n"
        storage = _make_storage(bad_content)
        log = AuditLog(storage)
        result = await log.read_day()
        assert len(result) == 1  # only valid entry returned

    async def test_record_chains_hashes(self) -> None:
        storage = _make_storage()
        written: list[bytes] = []

        async def capture_write(path: str, data: bytes, **kwargs: object) -> None:
            written.append(data)
            storage.read.side_effect = None
            storage.read.return_value = data

        storage.write.side_effect = capture_write

        log = AuditLog(storage)
        e1 = await log.record(AuditEventType.PURGE_REQUESTED, "USER-a", "USER-a")
        e2 = await log.record(AuditEventType.PURGE_CANCELLED, "USER-a", "USER-a")

        assert e2.prev_hash == e1.entry_hash

    async def test_day_path_format(self) -> None:
        d = date(2026, 5, 2)
        path = AuditLog._day_path(d)
        assert path == "_system/audit/2026/05/02.jsonl"

    async def test_record_legal_hold_released(self) -> None:
        storage = _make_storage()
        log = AuditLog(storage)
        entry = await log.record(
            AuditEventType.LEGAL_HOLD_RELEASED,
            actor_id="admin-001",
            subject_id="TASK-legal",
            metadata={"hold_reason": "litigation complete"},
        )
        assert entry.event_type == AuditEventType.LEGAL_HOLD_RELEASED
        assert entry.metadata["hold_reason"] == "litigation complete"

    async def test_record_right_to_erasure(self) -> None:
        storage = _make_storage()
        log = AuditLog(storage)
        entry = await log.record(
            AuditEventType.RIGHT_TO_ERASURE_REQUESTED,
            actor_id="USER-gdpr",
            subject_id="USER-gdpr",
            metadata={"justification": "GDPR Art. 17", "re_auth_at": "2026-05-02T10:00:00Z"},
        )
        assert entry.event_type == AuditEventType.RIGHT_TO_ERASURE_REQUESTED
