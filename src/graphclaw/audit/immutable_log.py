"""graphclaw.audit.immutable_log — Append-only audit log (FR-DEL-006/007).

Every lifecycle-sensitive action — purge request, cancel, legal-hold set/release,
right-to-erasure — is recorded here using admin_principal before the action
executes.  Entries are never modified or deleted; they are appended to a JSONL
file under ``_system/audit/{year}/{month}/{day}.jsonl`` in the configured
storage bucket.

Design notes
------------
- Entries are written via ``admin_principal``; no agent path can reach this module.
- Each entry carries a SHA-256 chain-hash of the previous entry, making tampering
  detectable (light tamper-evidence; not a full Merkle tree).
- The writer is intentionally **synchronous within the transaction** so that the
  caller can rely on the audit entry existing before returning to the client.
- Storage backend is injected (``StorageClient``); easy to swap to a write-once S3
  bucket or a WORM-enabled object store in production.
- Pattern: Strategy (storage backend) + Append-only Value Object (AuditEntry).

Methods
-------
- AuditLog.record(event_type, actor_id, subject_id, **metadata) -> AuditEntry
- AuditLog.read_day(date) -> list[AuditEntry]

Dependencies
------------
- graphclaw.infra.storage: StorageClient interface.
- hashlib, json, datetime: stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type catalogue
# ---------------------------------------------------------------------------


class AuditEventType(str, Enum):
    """Well-known lifecycle audit event types (FR-DEL-006/007/009)."""

    # Purge lifecycle
    PURGE_REQUESTED = "purge_requested"
    PURGE_CANCELLED = "purge_cancelled"
    PURGE_CONFIRMED = "purge_confirmed"
    PURGE_EXECUTED = "purge_executed"

    # Right to erasure (GDPR Article 17)
    RIGHT_TO_ERASURE_REQUESTED = "right_to_erasure_requested"
    RIGHT_TO_ERASURE_EXECUTED = "right_to_erasure_executed"

    # Legal hold
    LEGAL_HOLD_SET = "legal_hold_set"
    LEGAL_HOLD_RELEASED = "legal_hold_released"

    # Org archive
    ORG_ARCHIVED = "org_archived"
    ORG_ARCHIVE_CANCELLED = "org_archive_cancelled"


# ---------------------------------------------------------------------------
# Audit entry model
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single immutable audit log entry.

    Fields
    ------
    entry_id : str
        Unique identifier: ``{timestamp_ms}_{actor_id[:8]}``.
    timestamp : datetime
        UTC timestamp of the event.
    event_type : AuditEventType
        Category of the event.
    actor_id : str
        User ID or service principal that triggered the event.
    subject_id : str
        ID of the node that is the subject of the event.
    metadata : dict[str, Any]
        Arbitrary key/value context (justification, ip_address, etc.).
    prev_hash : str | None
        SHA-256 of the previous entry (hex).  ``None`` for the first entry.
    entry_hash : str
        SHA-256 of this entry's canonical JSON representation.
    """

    entry_id: str = Field(description="Unique entry ID.")
    timestamp: datetime = Field(description="UTC event timestamp.")
    event_type: AuditEventType = Field(description="Event category.")
    actor_id: str = Field(description="User/service that triggered this event.")
    subject_id: str = Field(description="Node that is the subject of the event.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str | None = Field(default=None, description="Hash of previous entry.")
    entry_hash: str = Field(description="SHA-256 of this entry's canonical JSON.")

    @classmethod
    def create(
        cls,
        event_type: AuditEventType,
        actor_id: str,
        subject_id: str,
        metadata: dict[str, Any] | None = None,
        prev_hash: str | None = None,
    ) -> AuditEntry:
        """Factory that auto-computes entry_id and entry_hash."""
        now = datetime.now(UTC)
        ts_ms = int(now.timestamp() * 1000)
        entry_id = f"{ts_ms}_{actor_id[:8]}"
        partial: dict[str, Any] = {
            "entry_id": entry_id,
            "timestamp": now.isoformat(),
            "event_type": event_type.value,
            "actor_id": actor_id,
            "subject_id": subject_id,
            "metadata": metadata or {},
            "prev_hash": prev_hash,
        }
        canonical = json.dumps(partial, sort_keys=True)
        entry_hash = hashlib.sha256(canonical.encode()).hexdigest()
        return cls(**partial, entry_hash=entry_hash)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_AUDIT_PREFIX = "_system/audit"


class AuditLog:
    """Append-only audit log backed by a StorageClient (S3/MinIO).

    Usage
    -----
    .. code-block:: python

        log = AuditLog(storage_client)
        entry = await log.record(
            AuditEventType.PURGE_REQUESTED,
            actor_id="USER-abc",
            subject_id="USER-abc",
            metadata={"reason": "user request"},
        )

    Thread safety
    -------------
    Each ``record()`` call reads the day-file, appends, and writes back.
    For high-throughput scenarios a WORM bucket + S3 append-PUT is preferred.
    In dev/test single-writer usage is safe.
    """

    def __init__(self, storage: StorageClient) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record(
        self,
        event_type: AuditEventType,
        actor_id: str,
        subject_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append a new entry to today's audit log file.

        Returns the newly created entry (for callers that need the entry_hash).
        """
        prev_hash = await self._last_hash_for_today()
        entry = AuditEntry.create(
            event_type=event_type,
            actor_id=actor_id,
            subject_id=subject_id,
            metadata=metadata,
            prev_hash=prev_hash,
        )
        await self._append(entry)
        logger.info(
            "audit: %s actor=%s subject=%s entry=%s",
            event_type.value,
            actor_id,
            subject_id,
            entry.entry_id,
        )
        return entry

    async def read_day(self, day: date | None = None) -> list[AuditEntry]:
        """Return all entries for *day* (defaults to today UTC)."""
        target = day or datetime.now(UTC).date()
        path = self._day_path(target)
        try:
            raw_bytes = await self._storage.read(path)
        except FileNotFoundError:
            return []
        raw = raw_bytes.decode("utf-8")
        entries: list[AuditEntry] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(AuditEntry.model_validate_json(line))
            except Exception:
                logger.warning("audit: skipping malformed entry in %s", path)
        return entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _last_hash_for_today(self) -> str | None:
        """Return the entry_hash of today's last entry, or None."""
        entries = await self.read_day()
        if not entries:
            return None
        return entries[-1].entry_hash

    async def _append(self, entry: AuditEntry) -> None:
        """Append *entry* as a JSONL line to today's log file."""
        path = self._day_path(datetime.now(UTC).date())
        try:
            existing_bytes = await self._storage.read(path)
            existing = existing_bytes.decode("utf-8")
        except FileNotFoundError:
            existing = ""
        new_content = existing.rstrip("\n") + "\n" + entry.model_dump_json() + "\n"
        await self._storage.write(
            path, new_content.encode("utf-8"), content_type="application/jsonl"
        )

    @staticmethod
    def _day_path(day: date) -> str:
        return f"{_AUDIT_PREFIX}/{day.year}/{day.month:02d}/{day.day:02d}.jsonl"
