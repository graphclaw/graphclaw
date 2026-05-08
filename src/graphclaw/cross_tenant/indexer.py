# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.cross_tenant.indexer — Event-bus consumer for org_task_index sync (FR-XT-001).

Description
-----------
``OrgTaskIndexer`` subscribes to the ``TASK_MUTATION_EVENTS`` broker queue and
keeps the ``org_task_index`` Postgres table in sync whenever tasks are created,
updated, or transition state.

Events consumed (JSON payloads):
  - ``{"event": "task_created",  "task_id": "...", "owner_user_id": "...", ...}``
  - ``{"event": "task_updated",  "task_id": "...", ...}``
  - ``{"event": "state_changed", "task_id": "...", "new_state": "..."}``
  - ``{"event": "task_archived", "task_id": "...", "archived_at": "..."}``

Task data is fetched from the graph store on each event so the index always
reflects the current authoritative state.

Design Patterns
---------------
- Background Task: ``start()`` / ``stop()`` lifecycle.
- Event Adapter: Translates broker events to ``OrgTaskIndex.upsert()`` calls.
- Graceful Degradation: Fetch / upsert failures are logged and skipped; the
  consumer loop never stops for a single bad message.

Public API
----------
- OrgTaskIndexer: Background consumer for task mutation events.

Dependencies
------------
- graphclaw.cross_tenant.task_index: OrgTaskIndex, OrgTaskIndexEntry.
- graphclaw.infra.broker: MessageBroker, TASK_MUTATION_EVENTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from graphclaw.cross_tenant.task_index import OrgTaskIndex, OrgTaskIndexEntry
from graphclaw.infra.broker import TASK_MUTATION_EVENTS, MessageBroker

logger = logging.getLogger(__name__)


class OrgTaskIndexer:
    """Keeps org_task_index in sync via task-mutation-event subscription (FR-XT-001).

    Parameters
    ----------
    broker:
        MessageBroker to consume TASK_MUTATION_EVENTS from.
    task_index:
        ``OrgTaskIndex`` instance for upsert and archival.
    store:
        GraphStore used to fetch the full task node on mutation events.
        When ``None``, the indexer uses only the payload fields (less complete).
    """

    def __init__(
        self,
        broker: MessageBroker,
        task_index: OrgTaskIndex,
        store: Any | None = None,
    ) -> None:
        self._broker = broker
        self._index = task_index
        self._store = store
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start background task-mutation-event consumption."""
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("OrgTaskIndexer: started")

    async def stop(self) -> None:
        """Gracefully stop background task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("OrgTaskIndexer: stopped")

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Consume TASK_MUTATION_EVENTS indefinitely."""
        async for raw in self._broker.consume(TASK_MUTATION_EVENTS):
            if not self._running:
                break
            try:
                payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                await self._dispatch(payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("OrgTaskIndexer: failed to handle event: %s — raw: %.200s", exc, raw)

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Route a task mutation payload to upsert or archive."""
        event = payload.get("event", "")
        task_id = payload.get("task_id", "")

        if not task_id:
            logger.warning("OrgTaskIndexer: missing task_id in payload: %s", payload)
            return

        if event == "task_archived":
            archived_at_str = payload.get("archived_at")
            archived_at = (
                datetime.fromisoformat(archived_at_str)
                if archived_at_str
                else datetime.now(timezone.utc)
            )
            await self._index.set_archived(task_id, archived_at)
            logger.info("OrgTaskIndexer: archived task_id=%s", task_id)
        elif event in ("task_created", "task_updated", "state_changed"):
            await self._upsert_from_event(task_id, payload)
        else:
            logger.debug("OrgTaskIndexer: unknown event '%s' — ignored", event)

    async def _upsert_from_event(self, task_id: str, payload: dict[str, Any]) -> None:
        """Build an OrgTaskIndexEntry and upsert it into the index.

        Prefers fetching full task data from the graph store; falls back to
        payload fields when store is unavailable.
        """
        node: dict[str, Any] | None = None
        if self._store is not None:
            try:
                node = await self._store.get_node(task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OrgTaskIndexer: get_node(%s) failed: %s", task_id, exc)

        if node is None:
            # Fall back to payload fields only
            node = payload

        owner_user_id = node.get("owner_user_id") or node.get("user_id") or ""
        org_id = node.get("org_id") or payload.get("org_id") or ""
        workspace_id = node.get("workspace_id") or payload.get("workspace_id")
        state = node.get("state") or payload.get("new_state") or payload.get("state") or "OPEN"
        summary = (node.get("title") or node.get("summary_text") or node.get("description") or "")[
            :500
        ]
        assignees_raw = node.get("assignee_linked_user_ids") or []
        if isinstance(assignees_raw, str):
            try:
                assignees_raw = json.loads(assignees_raw)
            except Exception:  # noqa: BLE001
                assignees_raw = []
        assignees: list[str] = list(assignees_raw) if assignees_raw else []

        # Parse deadline
        deadline = None
        raw_deadline = node.get("deadline") or node.get("due_date")
        if raw_deadline and isinstance(raw_deadline, str):
            try:
                deadline = datetime.fromisoformat(raw_deadline)
            except ValueError:
                pass
        elif isinstance(raw_deadline, datetime):
            deadline = raw_deadline

        entry = OrgTaskIndexEntry(
            task_id=task_id,
            owner_user_id=owner_user_id,
            org_id=org_id,
            workspace_id=workspace_id,
            assignee_linked_user_ids=assignees,
            state=state,
            deadline=deadline,
            last_activity_at=datetime.now(timezone.utc),
            summary_text=summary,
        )

        try:
            await self._index.upsert(entry)
            logger.info(
                "OrgTaskIndexer: upserted task_id=%s org=%s state=%s",
                task_id,
                org_id,
                state,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OrgTaskIndexer: upsert failed task_id=%s: %s", task_id, exc)
