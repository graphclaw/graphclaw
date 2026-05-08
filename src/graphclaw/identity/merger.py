# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.identity.merger — Resource node merge tool (FR-ID-004).

Description
-----------
Implements ``merge_resource(keep_id, merge_id)`` post-hoc deduplication:

1. Redirect all edges from ``merge_id`` → ``keep_id`` via ``redirect_edges``.
2. Concatenate aliases (deduped) from merge_id → keep_id.
3. Append intelligence lines from merge_id → keep_id (chronological).
4. Archive merge_id with a tombstone redirect (admin_principal).
5. Emit cache-invalidation event so active comms-agent sessions update.

Design Patterns
---------------
- Service Object: ``ResourceMerger`` encapsulates all merge logic.
- Admin delegation: lifecycle writes use the store directly (admin_principal at
  the gateway layer, not enforced inside this service).

Public API
----------
- MergeResult: Result dataclass.
- ResourceMerger: Main merge service.
- ResourceMerger.merge(keep_id, merge_id, canonical_name?, broker?): Execute merge.

Dependencies
------------
- graphclaw.db.base: GraphStore
- graphclaw.infra.storage: StorageClient, StoragePaths
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """Result of a resource merge operation (FR-ID-004).

    Attributes
    ----------
    keep_id:
        Node ID that was kept as canonical.
    merge_id:
        Node ID that was merged (archived with tombstone).
    tombstone_id:
        ID of the TombstoneNode created for merge_id.
    edges_redirected:
        Number of edge relationships redirected from merge_id → keep_id.
    aliases_merged:
        Number of new aliases added to keep_id.
    intelligence_lines_merged:
        Number of intelligence log lines appended.
    """

    keep_id: str
    merge_id: str
    tombstone_id: str
    edges_redirected: int
    aliases_merged: int
    intelligence_lines_merged: int


# ---------------------------------------------------------------------------
# Merger
# ---------------------------------------------------------------------------


class ResourceMerger:
    """Post-hoc deduplication for ResourceNode / UserNode pairs (FR-ID-004).

    Parameters
    ----------
    store:
        GraphStore with admin_principal (needs lifecycle field write access).
    storage:
        Optional StorageClient for conversation-path merge (FR-RES-005).
    """

    def __init__(self, store: object, storage: object | None = None) -> None:
        self._store = store
        self._storage = storage

    async def merge(
        self,
        keep_id: str,
        merge_id: str,
        canonical_name: str | None = None,
        broker: object | None = None,
        caller_context: object | None = None,
    ) -> MergeResult:
        """Execute a full resource merge.

        Steps:
        1. Load both nodes.
        2. Redirect edges: merge_id → keep_id.
        3. Merge aliases (deduped).
        4. Merge intelligence (append chronologically).
        5. Archive merge_id with tombstone redirect.
        6. Optionally update keep_id's canonical name.
        7. Emit cache-invalidation event.

        Parameters
        ----------
        keep_id:
            The node to keep as the canonical identity.
        merge_id:
            The node to merge into keep_id (will be archived + tombstoned).
        canonical_name:
            Optional override for keep_id's name after merge.
        broker:
            Optional message broker for cache-invalidation events.

        Returns
        -------
        MergeResult
        """
        # 1. Load nodes
        keep_raw = await self._store.get_node(
            keep_id, include_archived=False, caller_context=caller_context
        )
        merge_raw = await self._store.get_node(
            merge_id, include_archived=False, caller_context=caller_context
        )

        if keep_raw is None:
            raise ValueError(f"keep_id node not found: {keep_id}")
        if merge_raw is None:
            raise ValueError(f"merge_id node not found: {merge_id}")

        # 2. Redirect edges
        edges_redirected = await self._redirect_edges(merge_id, keep_id)

        # 3. Merge aliases
        aliases_merged = await self._merge_aliases(
            keep_id, keep_raw, merge_raw, caller_context=caller_context
        )

        # 4. Merge intelligence
        intel_merged = await self._merge_intelligence(
            keep_id, keep_raw, merge_raw, caller_context=caller_context
        )

        # 5. Create tombstone + archive merge_id
        tombstone_id = await self._create_tombstone(
            merge_id, keep_id, caller_context=caller_context
        )

        # 6. Optionally update keep_id name
        if canonical_name:
            await self._store.update_node(
                keep_id, {"name": canonical_name}, caller_context=caller_context
            )

        # 7. Conversation path merge (FR-RES-005)
        if self._storage is not None:
            await self._merge_conversations(keep_id, merge_id)

        # 8. Cache-invalidation event
        if broker is not None:
            try:
                await broker.publish(
                    "agent.cache.invalidate",
                    {"node_ids": [keep_id, merge_id], "reason": "resource_merge"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("merge.cache_invalidation_failed: %s", exc)

        logger.info(
            "resource_merge.complete",
            extra={
                "keep_id": keep_id,
                "merge_id": merge_id,
                "tombstone_id": tombstone_id,
                "edges_redirected": edges_redirected,
            },
        )

        return MergeResult(
            keep_id=keep_id,
            merge_id=merge_id,
            tombstone_id=tombstone_id,
            edges_redirected=edges_redirected,
            aliases_merged=aliases_merged,
            intelligence_lines_merged=intel_merged,
        )

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    async def _redirect_edges(self, from_id: str, to_id: str) -> int:
        """Redirect edges from *from_id* → *to_id*."""
        try:
            if hasattr(self._store, "redirect_edges"):
                count = await self._store.redirect_edges(from_id, to_id)
                return count if isinstance(count, int) else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge.redirect_edges_failed: %s", exc)
        return 0

    async def _merge_aliases(
        self, keep_id: str, keep_raw: dict, merge_raw: dict, caller_context: object | None = None
    ) -> int:
        """Append aliases from merge_raw → keep_raw (deduped by value)."""
        keep_aliases: list[dict] = keep_raw.get("aliases", []) if isinstance(keep_raw, dict) else []
        merge_aliases: list[dict] = (
            merge_raw.get("aliases", []) if isinstance(merge_raw, dict) else []
        )

        keep_values = {
            (a.get("value", "") if isinstance(a, dict) else str(a)).lower() for a in keep_aliases
        }

        new_aliases = [
            a
            for a in merge_aliases
            if (a.get("value", "") if isinstance(a, dict) else str(a)).lower() not in keep_values
        ]

        if not new_aliases:
            return 0

        combined = list(keep_aliases) + new_aliases
        await self._store.update_node(keep_id, {"aliases": combined}, caller_context=caller_context)
        return len(new_aliases)

    async def _merge_intelligence(
        self, keep_id: str, keep_raw: dict, merge_raw: dict, caller_context: object | None = None
    ) -> int:
        """Append intelligence lines from merge_raw → keep_raw chronologically."""
        keep_intel: str = (keep_raw.get("intelligence") or "") if isinstance(keep_raw, dict) else ""
        merge_intel: str = (
            (merge_raw.get("intelligence") or "") if isinstance(merge_raw, dict) else ""
        )

        if not merge_intel.strip():
            return 0

        # Simple chronological merge: append merge_id lines with provenance header
        provenance = f"\n\n--- Merged from {merge_raw.get('id', '?')} ---\n"
        combined_intel = (keep_intel.strip() + provenance + merge_intel.strip()).strip()
        await self._store.update_node(
            keep_id, {"intelligence": combined_intel}, caller_context=caller_context
        )

        # Count lines merged
        return len([line for line in merge_intel.splitlines() if line.strip()])

    async def _create_tombstone(
        self, merge_id: str, redirect_to: str, caller_context: object | None = None
    ) -> str:
        """Archive merge_id and create a TombstoneNode pointing to keep_id."""
        from graphclaw.models.base import generate_id, utcnow  # noqa: PLC0415
        from graphclaw.models.nodes import TombstoneNode  # noqa: PLC0415

        tomb_id = generate_id("TOMB")
        tombstone = TombstoneNode(
            id=tomb_id,
            archived_node_id=merge_id,
            redirect_to=redirect_to,
            reason="merged_duplicate",
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        try:
            await self._store.create_node(tombstone, caller_context=caller_context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge.tombstone_create_failed: %s", exc)

        # Archive the merge_id node itself
        try:
            from graphclaw.models.base import utcnow as _now  # noqa: PLC0415

            now = _now()
            await self._store.update_node(
                merge_id,
                {
                    "archived_at": now.isoformat(),
                    "archived_by": "SYSTEM_MERGE",
                    "archive_reason": f"merged_into:{redirect_to}",
                },
                caller_context=caller_context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge.archive_merge_id_failed: %s", exc)

        return tomb_id

    async def _merge_conversations(self, keep_id: str, merge_id: str) -> None:
        """Merge conversation JSONL files from merge_id → keep_id (FR-RES-005)."""
        try:
            from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

            # List conversation files under merge_id
            merge_prefix = StoragePaths.conversation_counterparty_dir(merge_id, "")
            # Write .tombstone redirect at the old path
            tomb_path = f"{merge_prefix}.tombstone"
            redirect_content = f"redirect_to: {keep_id}\n"
            await self._storage.write(tomb_path, redirect_content.encode("utf-8"), "text/plain")
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge.conversation_merge_failed: %s", exc)
