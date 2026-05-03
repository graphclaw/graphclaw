"""graphclaw.cascade.detachment — Counterparty detachment cascade (FR-AD-001).

Description
-----------
When a counterparty (ResourceNode with ``linked_user_id``) becomes unreachable
or the org membership is removed, ``DetachmentCascade`` freezes the last-known
data and sets link_status to ``detached_org_left``.

This prevents stale reads and ensures the agent surfaces the correct status
when composing messages to detached contacts.

Public API
----------
- DetachmentCascade: Main handler.
- DetachmentCascade.detach(node_id): Freeze node and update link_status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class DetachmentCascade:
    """Counterparty detachment cascade (FR-AD-001).

    Parameters
    ----------
    store:
        GraphStore for reading/updating nodes.
    storage:
        Optional StorageClient for archiving last-known profile snapshot.
    """

    def __init__(self, store: Any, storage: Any | None = None) -> None:
        self._store = store
        self._storage = storage

    async def detach(self, node_id: str, reason: str = "org_membership_removed") -> None:
        """Freeze *node_id* and set link_status to ``detached_org_left``.

        1. Update link_status on the node.
        2. Record ``detached_at`` timestamp.
        3. Archive a snapshot if storage is available (FR-AD-001 AC2).
        """
        logger.info(
            "detachment_cascade.detach",
            extra={"node_id": node_id, "reason": reason},
        )

        now = datetime.now(timezone.utc)
        try:
            await self._store.update_node(
                node_id,
                {
                    "link_status": "detached_org_left",
                    "detached_at": now.isoformat(),
                    "detach_reason": reason,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("detachment_cascade.update_node_failed: %s", exc)
            return

        if self._storage is not None:
            await self._archive_snapshot(node_id, now)

    async def _archive_snapshot(self, node_id: str, detached_at: datetime) -> None:
        """Archive a snapshot of the node at detachment time (FR-AD-001 AC2)."""
        try:
            raw = await self._store.get_node(node_id, include_archived=False)
            if raw is None:
                return
            import json  # noqa: PLC0415

            snapshot = {
                "node_id": node_id,
                "detached_at": detached_at.isoformat(),
                "snapshot": raw if isinstance(raw, dict) else {"id": node_id},
            }
            path = f"_system/detachments/{node_id}/{detached_at.strftime('%Y%m%d%H%M%S')}.json"
            await self._storage.write(
                path,
                json.dumps(snapshot).encode("utf-8"),
                "application/json",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("detachment_cascade.archive_snapshot_failed: %s", exc)
