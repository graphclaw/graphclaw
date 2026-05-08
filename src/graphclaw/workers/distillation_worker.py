# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.workers.distillation_worker — Distillation outbox worker (FR-RES-001).

Description
-----------
Background worker that processes pending entries in ``distillation_outbox``
and applies them to the target node's intelligence or working memory.

Retries up to ``MAX_RETRIES`` times; idempotency guaranteed by the unique
constraint in the outbox table.

Design Patterns
---------------
- Worker loop: ``DistillationWorker.run_once()`` processes all pending entries.
- Error isolation: Per-entry failures are logged; the worker continues.

Public API
----------
- DistillationWorker: Main worker class.
- DistillationWorker.run_once(): Process all pending entries.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


class DistillationWorker:
    """Processes pending distillation outbox entries (FR-RES-001).

    Parameters
    ----------
    outbox:
        ``DistillationOutbox`` instance.
    store:
        GraphStore for updating node intelligence.
    storage:
        Optional ``StorageClient`` for working memory writes.
    """

    def __init__(
        self,
        outbox: Any,
        store: Any,
        storage: Any | None = None,
    ) -> None:
        self._outbox = outbox
        self._store = store
        self._storage = storage

    async def run_once(self) -> int:
        """Process all pending outbox entries.

        Returns
        -------
        int
            Number of entries successfully processed.
        """
        pending = await self._outbox.list_pending()
        processed = 0

        for entry in pending:
            if entry.retry_count >= MAX_RETRIES:
                # Skip permanently failed entries (no more retries)
                logger.warning(
                    "distillation_worker.max_retries_exceeded",
                    extra={"entry_id": entry.id, "target": entry.target_node_id},
                )
                continue

            try:
                await self._apply_write(entry)
                await self._outbox.mark_processed(entry.id)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                error_str = str(exc)
                logger.warning(
                    "distillation_worker.entry_failed",
                    extra={"entry_id": entry.id, "error": error_str},
                )
                await self._outbox.mark_failed(entry.id, error_str)

        return processed

    async def _apply_write(self, entry: Any) -> None:
        """Apply a single distillation write to the target."""
        if entry.target_type == "intelligence":
            await self._apply_intelligence_write(entry)
        elif entry.target_type == "memory_note":
            await self._apply_memory_note_write(entry)
        else:
            logger.warning("distillation_worker.unknown_target_type: %s", entry.target_type)

    async def _apply_intelligence_write(self, entry: Any) -> None:
        """Append an intelligence line to a node's ``intelligence`` field."""
        line = entry.payload.get("line", "")
        if not line:
            return

        node_raw = await self._store.get_node(entry.target_node_id, include_archived=False)
        if node_raw is None:
            raise ValueError(f"Node not found: {entry.target_node_id}")

        current = node_raw.get("intelligence") or "" if isinstance(node_raw, dict) else ""
        updated = (current.strip() + "\n" + line).strip() if current else line
        await self._store.update_node(entry.target_node_id, {"intelligence": updated})

    async def _apply_memory_note_write(self, entry: Any) -> None:
        """Append a memory note to ``working/context.md``."""
        if self._storage is None:
            raise ValueError("storage not configured for memory_note writes")

        user_id = entry.payload.get("user_id", "")
        agent_id = entry.payload.get("agent_id", "main")
        note = entry.payload.get("note", "")
        if not (user_id and note):
            return

        from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

        path = StoragePaths.working_context(user_id, agent_id)
        try:
            existing_bytes = await self._storage.read(path)
            existing = existing_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            existing = ""

        updated = (existing.strip() + "\n\n" + note).strip() if existing else note
        await self._storage.write(path, updated.encode("utf-8"), "text/markdown")
