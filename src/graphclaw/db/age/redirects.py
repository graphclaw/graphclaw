"""graphclaw.db.age.redirects — TombstoneNode resolver primitive (FR-DEL-003).

Description
-----------
Provides ``resolve_canonical(node_id, store, max_hops)`` which follows a chain
of TombstoneNode redirects to locate the current live node.

Design
------
When a node is archived, a ``TombstoneNode`` is written to the graph with
``archived_node_id = original_id`` and ``redirect_to = replacement_id | None``.
The original node gets ``link_status = "redirected"``.

``resolve_canonical`` performs a hop-by-hop resolution:
1. Look up ``node_id`` in the graph.
2. If it has ``link_status = "redirected"`` or ``archived_at IS NOT NULL``,
   find the TombstoneNode where ``archived_node_id = node_id``.
3. Follow ``redirect_to`` up to ``max_hops`` times.
4. Raises ``TombstoneCycle`` if a cycle is detected.
5. Raises ``MaxHopsExceeded`` if the chain exceeds ``max_hops``.

Public API
----------
- TombstoneCycle: Exception — cycle detected in redirect chain.
- MaxHopsExceeded: Exception — redirect chain too long.
- resolve_canonical: Follow tombstone redirects to the live node.

Dependencies
------------
- graphclaw.db.base: GraphStore.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)

# Default maximum hops allowed when following redirect chains.
DEFAULT_MAX_HOPS: int = 5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TombstoneCycle(Exception):
    """Raised when resolve_canonical detects a cycle in the redirect chain."""


class MaxHopsExceeded(Exception):
    """Raised when the redirect chain exceeds the max_hops limit."""


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


async def resolve_canonical(
    node_id: str,
    store: GraphStore,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> dict | None:
    """Follow TombstoneNode redirects to the current live node.

    Parameters
    ----------
    node_id:
        The ID of the node to resolve.  May be a live node or an archived
        one with a ``link_status = "redirected"`` marker.
    store:
        An open ``GraphStore`` instance (any principal — reads only).
    max_hops:
        Maximum number of redirect hops to follow before raising
        ``MaxHopsExceeded``.  Defaults to ``DEFAULT_MAX_HOPS`` (5).

    Returns
    -------
    dict | None
        The properties dict of the current live node, or ``None`` if the
        final redirect target is ``None`` (node deleted with no replacement).

    Raises
    ------
    TombstoneCycle
        When the redirect chain contains a cycle (A → B → A).
    MaxHopsExceeded
        When the chain length exceeds ``max_hops``.

    Notes
    -----
    AC1: A→B→C where A tombstoned → B and B tombstoned → C resolves to C.
    AC2: A→B→A raises TombstoneCycle.
    AC3: Default reads (store.get_node with include_archived=False) exclude
         archived nodes; this resolver explicitly handles the redirect.
    """
    visited: set[str] = set()
    current_id = node_id
    hops = 0

    while hops <= max_hops:
        if current_id in visited:
            raise TombstoneCycle(
                f"Cycle detected in tombstone redirect chain: "
                f"{' → '.join(list(visited))} → {current_id}"
            )
        visited.add(current_id)

        node = await store.get_node(current_id, include_archived=True)

        if node is None:
            # Node not found — may have been purged without a tombstone.
            logger.debug(
                "resolve_canonical: node not found at hop %d",
                hops,
                extra={"node_id": current_id, "hops": hops},
            )
            return None

        link_status = node.get("link_status")
        archived_at = node.get("archived_at")

        # If the node is live (not redirected, not archived), return it.
        if link_status != "redirected" and not archived_at:
            logger.debug(
                "resolve_canonical: resolved to live node",
                extra={"original_id": node_id, "resolved_id": current_id, "hops": hops},
            )
            return node

        # Node is archived or redirected — find its TombstoneNode.
        tombstone = await _find_tombstone(current_id, store)
        if tombstone is None:
            # Archived but no tombstone found — return as-is (best effort).
            logger.warning(
                "resolve_canonical: archived node has no tombstone",
                extra={"node_id": current_id},
            )
            return node

        redirect_to = tombstone.get("redirect_to")
        if redirect_to is None:
            # Tombstone with no redirect — node is gone, no replacement.
            logger.debug(
                "resolve_canonical: tombstone has no redirect (node deleted)",
                extra={"node_id": current_id},
            )
            return None

        # Follow the redirect.
        current_id = redirect_to
        hops += 1

    raise MaxHopsExceeded(
        f"Redirect chain from {node_id!r} exceeds max_hops={max_hops}. "
        f"Visited: {' → '.join(sorted(visited))}"
    )


async def _find_tombstone(archived_node_id: str, store: GraphStore) -> dict | None:
    """Return the TombstoneNode for *archived_node_id*, or None if absent."""
    tombstones = await store.list_nodes(
        "TombstoneNode",
        filters={"archived_node_id": archived_node_id},
    )
    if not tombstones:
        return None
    # If multiple tombstones exist (unusual), prefer the most recent.
    return sorted(tombstones, key=lambda t: t.get("created_at", ""), reverse=True)[0]
