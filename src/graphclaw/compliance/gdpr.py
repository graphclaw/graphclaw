"""graphclaw.compliance.gdpr — GDPR right-to-erasure orchestration.

Description
-----------
Provides ``GDPRService``, which implements the GDPR right-to-erasure workflow:
accepting erasure requests, anonymising ``UserNode`` data in the property graph,
deleting all owned ``TaskNode`` and ``VisibilityGrantNode`` records, removing S3
objects, and pruning old audit log entries while preserving recent entries for
compliance purposes.

Design Patterns
---------------
- Dependency Injection: ``GraphStore``, ``StorageClient``, and ``AuditLogger``
  are injected so the service is fully testable and backend-agnostic.
- Best-effort cleanup: Individual deletion failures are logged but do not abort
  the overall erasure; the service continues to clean up as much as possible
  before returning a status.

Public API
----------
- GDPRService: Main orchestration class with request_erasure, process_erasure,
  get_erasure_status.

Dependencies
------------
- graphclaw.compliance.audit: AuditLogger.
- graphclaw.compliance.models: AuditEvent, ErasureRequest, ErasureStatus.
- graphclaw.db.base: GraphStore ABC.
- graphclaw.infra.storage: StorageClient ABC.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from graphclaw.compliance.audit import AuditLogger
from graphclaw.compliance.models import AuditEvent, ErasureRequest, ErasureStatus
from graphclaw.db.base import GraphStore
from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)

# In-memory store mapping request_id -> ErasureStatus for this session.
# A production implementation would persist this in the graph or a dedicated table.
_erasure_statuses: dict[str, ErasureStatus] = {}

# Days of audit log to preserve for compliance purposes after erasure.
_AUDIT_RETENTION_DAYS: int = 30


class GDPRService:
    """Orchestrates GDPR right-to-erasure requests.

    Parameters
    ----------
    graph_store:
        Graph backend used to anonymise ``UserNode`` records and delete
        ``TaskNode`` and ``VisibilityGrantNode`` vertices.
    storage:
        Object-storage backend used to delete the user's S3 prefix.
    audit_logger:
        Audit logger for recording request and completion events.
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

    async def request_erasure(
        self,
        user_id: str,
        requester_email: str,
        reason: str = "",
    ) -> ErasureRequest:
        """Create a new erasure request and log the audit event.

        Parameters
        ----------
        user_id:
            The ``USER-{uuid}`` identifier of the subject.
        requester_email:
            Email of the person submitting the request.
        reason:
            Optional free-text reason.

        Returns
        -------
        ErasureRequest
            The newly created request.
        """
        request_id = f"ERASURE-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        request = ErasureRequest(
            user_id=user_id,
            requested_at=now,
            requester_email=requester_email,
            request_id=request_id,
            reason=reason,
        )
        _erasure_statuses[request_id] = ErasureStatus.PENDING

        audit_event = AuditEvent(
            event_id=f"AUDIT-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            action="erasure.requested",
            resource_type="UserNode",
            resource_id=user_id,
            timestamp=now,
            metadata={
                "request_id": request_id,
                "requester_email": requester_email,
                "reason": reason,
            },
        )
        await self._audit.log(audit_event)
        logger.info(
            "gdpr: erasure request created",
            extra={"request_id": request_id, "user_id": user_id},
        )
        return request

    async def process_erasure(self, request: ErasureRequest) -> ErasureStatus:
        """Execute all erasure steps for the given request.

        Steps performed:
        1. Anonymise ``UserNode`` in the property graph.
        2. Delete all ``TaskNode`` records owned by the user.
        3. Delete all ``VisibilityGrantNode`` records for the user.
        4. Delete all S3 objects under ``{user_id}/`` prefix.
        5. Delete audit log entries older than 30 days.
        6. Log ``erasure.completed`` audit event.

        Parameters
        ----------
        request:
            The ``ErasureRequest`` to process.

        Returns
        -------
        ErasureStatus
            ``COMPLETED`` on success; ``FAILED`` if any step raises an
            unhandled exception.
        """
        _erasure_statuses[request.request_id] = ErasureStatus.PROCESSING
        user_id = request.user_id

        try:
            # Step 1: Anonymise UserNode
            await self._graph.update_node(
                user_id,
                {
                    "name": "[deleted]",
                    "email": f"deleted-{user_id}@anon.graphclaw.ai",
                    "phone": None,
                },
            )
            logger.debug("gdpr: UserNode anonymised", extra={"user_id": user_id})

            # Step 2: Delete all TaskNodes owned by user
            tasks = await self._graph.list_nodes("TaskNode", {"owner_id": user_id})
            for task in tasks:
                task_id: str = task.get("id", "")
                if task_id:
                    try:
                        await self._graph.delete_node(task_id)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "gdpr: failed to delete TaskNode",
                            exc_info=True,
                            extra={"task_id": task_id},
                        )
            logger.debug(
                "gdpr: TaskNodes deleted", extra={"count": len(tasks), "user_id": user_id}
            )

            # Step 3: Delete all VisibilityGrantNodes for user
            grants = await self._graph.list_nodes(
                "VisibilityGrantNode", {"granted_to_user_id": user_id}
            )
            for grant in grants:
                grant_id: str = grant.get("id", "")
                if grant_id:
                    try:
                        await self._graph.delete_node(grant_id)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "gdpr: failed to delete VisibilityGrantNode",
                            exc_info=True,
                            extra={"grant_id": grant_id},
                        )
            logger.debug(
                "gdpr: VisibilityGrantNodes deleted",
                extra={"count": len(grants), "user_id": user_id},
            )

            # Step 4: Delete S3 objects under {user_id}/ prefix
            s3_prefix = f"{user_id}/"
            try:
                keys = await self._storage.list_objects(s3_prefix)
                for key in keys:
                    try:
                        await self._storage.delete(key)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "gdpr: failed to delete S3 object",
                            exc_info=True,
                            extra={"key": key},
                        )
                logger.debug(
                    "gdpr: S3 objects deleted",
                    extra={"count": len(keys), "prefix": s3_prefix},
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "gdpr: failed to list S3 objects for prefix=%s", s3_prefix, exc_info=True
                )

            # Step 5: Delete audit log entries older than 30 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=_AUDIT_RETENTION_DAYS)
            audit_prefix = f"audit/{user_id}/"
            try:
                audit_keys = await self._storage.list_objects(audit_prefix)
                for key in audit_keys:
                    # Key format: audit/{user_id}/{YYYY-MM}/{event_id}.json
                    # Parse the month from the key to determine age.
                    parts = key.split("/")
                    if len(parts) >= 3:
                        month_str = parts[2]  # YYYY-MM
                        try:
                            month_dt = datetime.strptime(month_str, "%Y-%m").replace(
                                tzinfo=timezone.utc
                            )
                            # If the entire month is older than the cutoff, delete.
                            if month_dt < cutoff.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
                                try:
                                    await self._storage.delete(key)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "gdpr: failed to delete audit key=%s", key, exc_info=True
                                    )
                        except ValueError:
                            pass  # non-matching key structure; skip
            except Exception:  # noqa: BLE001
                logger.warning(
                    "gdpr: failed to list audit objects for prefix=%s",
                    audit_prefix,
                    exc_info=True,
                )

            # Step 6: Log completion audit event
            completion_event = AuditEvent(
                event_id=f"AUDIT-{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                action="erasure.completed",
                resource_type="UserNode",
                resource_id=user_id,
                timestamp=datetime.now(timezone.utc),
                metadata={"request_id": request.request_id},
            )
            await self._audit.log(completion_event)

            _erasure_statuses[request.request_id] = ErasureStatus.COMPLETED
            logger.info(
                "gdpr: erasure completed",
                extra={"request_id": request.request_id, "user_id": user_id},
            )
            return ErasureStatus.COMPLETED

        except Exception:  # noqa: BLE001
            _erasure_statuses[request.request_id] = ErasureStatus.FAILED
            logger.error(
                "gdpr: erasure failed",
                exc_info=True,
                extra={"request_id": request.request_id, "user_id": user_id},
            )
            return ErasureStatus.FAILED

    async def get_erasure_status(
        self, request_id: str, user_id: str  # noqa: ARG002
    ) -> ErasureStatus:
        """Return the current status of an erasure request.

        Parameters
        ----------
        request_id:
            The ``ERASURE-{...}`` identifier of the request.
        user_id:
            The user_id of the subject (used for authorisation checks by
            callers; not used in the current stub lookup).

        Returns
        -------
        ErasureStatus
            Current status, or ``PENDING`` if not found.
        """
        return _erasure_statuses.get(request_id, ErasureStatus.PENDING)


__all__ = ["GDPRService"]
