# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.agent_channel_identity — AgentChannelIdentity registry (FR-IN-003).

Description
-----------
``AgentChannelIdentityRegistry`` maintains an in-memory index of
``AgentChannelIdentity`` records, keyed by ``(channel, account_id)``.  It is:

- Loaded at gateway startup from the graph store.
- Hot-reloaded on admin CRUD operations.
- Used by ``InboundRouter.classify()`` to map receiving accounts to owner agents.

Design Patterns
---------------
- Registry / In-memory cache: O(1) lookup after startup load.
- Observer: admin API triggers ``invalidate()`` / ``add()`` / ``remove()`` after writes.
- Graceful degradation: empty registry returns ``None`` for all lookups (no
  crash, just ``drop`` route).

Public API
----------
- AgentChannelIdentityRegistry: In-memory registry.
  - lookup(channel, account_id) → AgentChannelIdentity | None
  - is_owner_identity(user_id, channel, sender_id) → bool
  - load_from_list(entries)
  - add(entry) / remove(channel, account_id)
  - all_entries() → list[AgentChannelIdentity]

Dependencies
------------
- graphclaw.models.agent_channel_identity: AgentChannelIdentity.
"""

from __future__ import annotations

import logging

from graphclaw.models.agent_channel_identity import AgentChannelIdentity

logger = logging.getLogger(__name__)


class AgentChannelIdentityRegistry:
    """In-memory registry mapping (channel, account_id) → AgentChannelIdentity.

    Parameters
    ----------
    entries:
        Initial list of ``AgentChannelIdentity`` records.  May be empty; use
        ``load_from_list()`` to populate after construction.
    """

    def __init__(self, entries: list[AgentChannelIdentity] | None = None) -> None:
        # Key: (channel, account_id) → AgentChannelIdentity
        self._index: dict[tuple[str, str], AgentChannelIdentity] = {}
        if entries:
            self.load_from_list(entries)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_list(self, entries: list[AgentChannelIdentity]) -> None:
        """Replace the in-memory index with *entries*."""
        self._index = {(e.channel, e.account_id): e for e in entries}
        logger.debug("AgentChannelIdentityRegistry: loaded %d entries", len(self._index))

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def lookup(self, *, channel: str, account_id: str) -> AgentChannelIdentity | None:
        """Return the identity entry for (*channel*, *account_id*), or ``None``.

        Returns ``None`` when:
        - No entry matches.
        - Entry exists but ``active == False``.
        """
        entry = self._index.get((channel, account_id))
        if entry is None:
            return None
        if not entry.active:
            logger.debug(
                "AgentChannelIdentityRegistry: entry disabled for %s/%s",
                channel,
                account_id,
            )
            return None
        return entry

    async def is_owner_identity(self, *, user_id: str, channel: str, sender_id: str) -> bool:
        """Return True if *sender_id* is a known identity of *user_id* on *channel*.

        This is used by ``InboundRouter`` to classify messages from the owner
        themselves as ``user_chat``.

        The check covers:
        1. ``AgentChannelIdentity.owner_identities`` — explicit list of owner
           sender IDs registered by the admin.
        2. Any entry where ``user_id`` matches and ``account_id == sender_id``
           (e.g. owner's own mailbox).
        """
        for entry in self._index.values():
            if entry.user_id != user_id or entry.channel != channel:
                continue
            if sender_id in entry.owner_identities:
                return True
            if entry.account_id == sender_id:
                return True
        return False

    # ------------------------------------------------------------------
    # CRUD (hot-reload)
    # ------------------------------------------------------------------

    def add(self, entry: AgentChannelIdentity) -> None:
        """Add or replace an entry (hot-reload on admin write)."""
        self._index[(entry.channel, entry.account_id)] = entry
        logger.debug(
            "AgentChannelIdentityRegistry: added %s/%s → user=%s",
            entry.channel,
            entry.account_id,
            entry.user_id,
        )

    def remove(self, *, channel: str, account_id: str) -> None:
        """Remove an entry (hot-reload on admin delete)."""
        self._index.pop((channel, account_id), None)
        logger.debug("AgentChannelIdentityRegistry: removed %s/%s", channel, account_id)

    def all_entries(self) -> list[AgentChannelIdentity]:
        """Return all registered entries (active and inactive)."""
        return list(self._index.values())

    def __len__(self) -> int:
        return len(self._index)
