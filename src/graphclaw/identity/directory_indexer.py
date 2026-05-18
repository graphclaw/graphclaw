# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.identity.directory_indexer — Event-bus consumer for user_directory sync (FR-DIR-001).

Description
-----------
``DirectoryIndexer`` subscribes to the ``MEMBERSHIP_EVENTS`` broker queue and
keeps the ``user_directory`` Postgres table in sync when org membership changes.

Events consumed (JSON payloads):
  - ``{"event": "member_added",   "user_id": "...", "org_id": "..."}``
  - ``{"event": "member_removed", "user_id": "...", "org_id": "..."}``
  - ``{"event": "profile_updated","user_id": "...", "org_id": "..."}``

Fan-out is delegated entirely to ``MembershipCascade`` so all cascade logic
stays in one place and this module is purely the event-bus adapter.

Design Patterns
---------------
- Background Task: ``start()`` / ``stop()`` lifecycle compatible with FastAPI
  lifespan and AgentEventConsumer.
- Event Adapter: Decodes raw broker messages; delegates processing to
  ``MembershipCascade``.
- Dependency Injection: All collaborators injected at construction — no singletons.

Public API
----------
- DirectoryIndexer: Background consumer for membership events.

Dependencies
------------
- graphclaw.cascade.membership: MembershipCascade.
- graphclaw.infra.broker: MessageBroker, MEMBERSHIP_EVENTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from graphclaw.infra.broker import MEMBERSHIP_EVENTS, MessageBroker

logger = logging.getLogger(__name__)


class DirectoryIndexer:
    """Keeps user_directory in sync via membership-event subscription (FR-DIR-001).

    Parameters
    ----------
    broker:
        MessageBroker to consume MEMBERSHIP_EVENTS from.
    cascade:
        MembershipCascade for on_member_added / on_member_removed fan-out.
    """

    def __init__(
        self,
        broker: MessageBroker,
        cascade: Any,
    ) -> None:
        self._broker = broker
        self._cascade = cascade
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start background membership-event consumption."""
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("DirectoryIndexer: started")

    async def stop(self) -> None:
        """Gracefully stop background task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DirectoryIndexer: stopped")

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Consume MEMBERSHIP_EVENTS indefinitely, routing to cascade handlers."""
        async for raw in self._broker.consume(MEMBERSHIP_EVENTS):
            if not self._running:
                break
            try:
                payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                await self._dispatch(payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("DirectoryIndexer: failed to handle event: %s — raw: %.200s", exc, raw)

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Route a membership event payload to the appropriate cascade method."""
        event = payload.get("event", "")
        user_id = payload.get("user_id", "")
        org_id = payload.get("org_id", "")

        if not user_id or not org_id:
            logger.warning("DirectoryIndexer: missing user_id/org_id in payload: %s", payload)
            return

        if event == "member_added":
            await self._cascade.on_member_added(user_id, org_id)
            logger.info("DirectoryIndexer: member_added user=%s org=%s", user_id, org_id)
        elif event == "member_removed":
            await self._cascade.on_member_removed(user_id, org_id)
            logger.info("DirectoryIndexer: member_removed user=%s org=%s", user_id, org_id)
        elif event == "profile_updated":
            # Profile update is treated as re-add to refresh all org rows
            await self._cascade.on_member_added(user_id, org_id)
            logger.info("DirectoryIndexer: profile_updated user=%s org=%s", user_id, org_id)
        else:
            logger.debug("DirectoryIndexer: unknown event type '%s' — ignored", event)
