# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.compliance.export — User data export service (GDPR portability).

Description
-----------
Provides ``DataExportService``, which collects all data owned by a user
(tasks, user profile, visibility grants, recent audit events) and serialises
it to a single JSON archive stored in object storage.  The resulting
``DataExport`` manifest includes the S3 key and a 7-day expiry timestamp
so callers can generate a time-limited presigned download URL.

Design Patterns
---------------
- Dependency Injection: ``GraphStore``, ``StorageClient``, and ``AuditLogger``
  are injected for testability and backend independence.
- Value Object Result: ``DataExport`` is a frozen dataclass so the manifest
  is safely immutable after creation.

Public API
----------
- DataExportService: Main class — export_user_data, get_export_download_url.

Dependencies
------------
- graphclaw.compliance.audit: AuditLogger.
- graphclaw.compliance.models: AuditEvent, DataExport.
- graphclaw.db.base: GraphStore ABC.
- graphclaw.infra.storage: StorageClient ABC.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.models import AuditEvent, DataExport
from graphclaw.db.base import GraphStore
from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)

# How long export archives are retained before expiry.
_EXPORT_TTL_DAYS: int = 7

# How many days of audit history to include in the export.
_AUDIT_HISTORY_DAYS: int = 90


class DataExportService:
    """Collects and serialises all user data to a JSON archive in object storage.

    Parameters
    ----------
    graph_store:
        Graph backend used to fetch ``TaskNode``, ``UserNode``, and
        ``VisibilityGrantNode`` records.
    storage:
        Object-storage backend used to write the export archive.
    audit_logger:
        Audit logger used to retrieve recent audit events and to record
        the export action itself.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        storage: StorageClient,
        audit_logger: AuditLogger,
    ) -> None:
        self._graph = graph_store
        self._storage = storage
        self._audit = audit_logger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def export_user_data(self, user_id: str) -> DataExport:
        """Collect all user data and write it to object storage.

        Collects:
        - All ``TaskNode`` records where ``owner_id == user_id``.
        - The ``UserNode`` record.
        - All ``VisibilityGrantNode`` records where
          ``granted_to_user_id == user_id``.
        - Audit events from the last 90 days.

        The collected data is serialised to a single JSON document and
        written to ``exports/{user_id}/{export_id}/data.json``.

        Parameters
        ----------
        user_id:
            The ``USER-{uuid}`` identifier of the user to export.

        Returns
        -------
        DataExport
            Manifest with the storage key, export ID, and expiry timestamp.
        """
        export_id = f"EXPORT-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=_EXPORT_TTL_DAYS)

        # Collect TaskNodes
        tasks: list[dict] = []
        try:
            tasks = await self._graph.list_nodes("TaskNode", {"owner_id": user_id})
        except Exception:  # noqa: BLE001
            logger.warning(
                "export: failed to list TaskNodes for user_id=%s", user_id, exc_info=True
            )

        # Collect UserNode
        user_node: dict | None = None
        try:
            user_node = await self._graph.get_node(user_id)
        except Exception:  # noqa: BLE001
            logger.warning("export: failed to get UserNode for user_id=%s", user_id, exc_info=True)

        # Collect VisibilityGrantNodes
        grants: list[dict] = []
        try:
            grants = await self._graph.list_nodes(
                "VisibilityGrantNode", {"granted_to_user_id": user_id}
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "export: failed to list VisibilityGrantNodes for user_id=%s",
                user_id,
                exc_info=True,
            )

        # Collect audit events (last 90 days)
        audit_events_raw: list[dict] = []
        try:
            audit_start = now - timedelta(days=_AUDIT_HISTORY_DAYS)
            audit_events = await self._audit.get_events(user_id=user_id, start=audit_start, end=now)
            audit_events_raw = [
                {
                    "event_id": e.event_id,
                    "action": e.action,
                    "resource_type": e.resource_type,
                    "resource_id": e.resource_id,
                    "timestamp": e.timestamp.isoformat(),
                    "ip_address": e.ip_address,
                    "metadata": e.metadata,
                }
                for e in audit_events
            ]
        except Exception:  # noqa: BLE001
            logger.warning(
                "export: failed to collect audit events for user_id=%s",
                user_id,
                exc_info=True,
            )

        record_count = (
            len(tasks) + (1 if user_node is not None else 0) + len(grants) + len(audit_events_raw)
        )

        export_payload: dict = {
            "export_id": export_id,
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "record_count": record_count,
            "user_node": user_node,
            "tasks": tasks,
            "visibility_grants": grants,
            "audit_events": audit_events_raw,
        }

        storage_key = f"exports/{user_id}/{export_id}/data.json"
        data = json.dumps(export_payload, default=str).encode()
        await self._storage.write(storage_key, data, content_type="application/json")

        # Log the export action
        audit_event = AuditEvent(
            event_id=f"AUDIT-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            action="data.exported",
            resource_type="UserNode",
            resource_id=user_id,
            timestamp=now,
            metadata={"export_id": export_id, "record_count": record_count},
        )
        await self._audit.log(audit_event)

        logger.info(
            "export: user data export completed",
            extra={"export_id": export_id, "user_id": user_id, "record_count": record_count},
        )

        return DataExport(
            user_id=user_id,
            export_id=export_id,
            created_at=now,
            storage_key=storage_key,
            expires_at=expires_at,
            record_count=record_count,
        )

    async def get_export_download_url(self, export: DataExport) -> str:
        """Return the storage key for the export archive.

        Callers are responsible for generating a time-limited presigned URL
        from this key using their storage backend.

        Parameters
        ----------
        export:
            The ``DataExport`` manifest returned by ``export_user_data``.

        Returns
        -------
        str
            The S3 object key (e.g. ``exports/{user_id}/{export_id}/data.json``).
        """
        return export.storage_key


__all__ = ["DataExportService"]
