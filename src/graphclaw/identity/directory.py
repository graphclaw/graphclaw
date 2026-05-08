# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.identity.directory — Org-scoped user directory read API (FR-DIR-001..002).

Description
-----------
Provides ``UserDirectory`` for fast fuzzy search over the ``user_directory``
Postgres table (created by migration 0020).  Every query is scoped to the
caller's org memberships — cross-org leaks are impossible by design.

Design Patterns
---------------
- Repository: ``UserDirectory`` is a thin async read-only repository over
  the Postgres ``user_directory`` table.
- Mandatory ACL: Every query method requires ``caller_org_ids`` — passing an
  empty list returns an empty result, never leaks cross-org data (NFR-004).

Public API
----------
- DirectoryEntry: Row DTO.
- UserDirectory: Async search API.
- UserDirectory.search(query, caller_org_ids): Trigram fuzzy search.
- UserDirectory.get_by_user_id(user_id, caller_org_ids): Exact lookup.

Dependencies
------------
- asyncpg: DB pool (passed in at construction).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class DirectoryEntry:
    """A row from the ``user_directory`` table.

    Attributes
    ----------
    user_id:
        Platform user ID.
    org_id:
        Org this row belongs to.
    display_name:
        User's canonical display name.
    emails:
        List of known email addresses.
    identities:
        Channel identities JSONB dict.
    discoverable_aliases:
        Searchable alias tokens.
    visibility_policy:
        ``"org_default"`` | ``"discoverable"`` | ``"name_only"`` | ``"hidden"``.
    last_updated:
        When this row was last refreshed.
    """

    user_id: str
    org_id: str
    display_name: str
    emails: list[str]
    identities: dict
    discoverable_aliases: list[str]
    visibility_policy: str
    last_updated: Any | None = None


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------


class UserDirectory:
    """Org-scoped user directory for fuzzy name + alias search (FR-DIR-001).

    Parameters
    ----------
    pool:
        Async DB pool with a ``fetch(sql, *args)`` interface (asyncpg style).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def search(
        self,
        query: str,
        caller_org_ids: list[str],
        *,
        limit: int = 10,
    ) -> list[DirectoryEntry]:
        """Fuzzy-search users by display_name or alias in *caller_org_ids*.

        Parameters
        ----------
        query:
            Free-text search string.
        caller_org_ids:
            Org IDs to scope the search to (FR-DIR-002 enforcement).
        limit:
            Max results to return.

        Returns
        -------
        list[DirectoryEntry]
            Matching rows, ordered by trigram similarity descending.
        """
        if not caller_org_ids:
            return []

        if self._pool is None:
            return []

        # Build parameterised org-list placeholder  (%s, %s, …)
        placeholders = ", ".join("%s" for _ in caller_org_ids)
        sql = f"""
            SELECT
                user_id, org_id, display_name, emails, identities,
                discoverable_aliases, visibility_policy, last_updated
            FROM user_directory
            WHERE org_id IN ({placeholders})
              AND visibility_policy <> 'hidden'
              AND (
                  display_name ILIKE %s
                  OR EXISTS (
                      SELECT 1 FROM unnest(discoverable_aliases) AS a WHERE a ILIKE %s
                  )
              )
            ORDER BY similarity(display_name, %s) DESC
            LIMIT {int(limit)}
        """
        query_pattern = f"%{query}%"
        try:
            rows = await self._pool.fetch(
                sql, *caller_org_ids, query_pattern, query_pattern, query_pattern
            )
            return [self._row_to_entry(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("user_directory.search_failed: %s", exc)
            return []

    async def get_by_user_id(self, user_id: str, caller_org_ids: list[str]) -> list[DirectoryEntry]:
        """Return directory rows for *user_id* scoped to *caller_org_ids*.

        Returns multiple rows when a user appears in multiple orgs the caller
        shares (FR-DIR-002).
        """
        if not caller_org_ids or not user_id:
            return []

        if self._pool is None:
            return []

        placeholders = ", ".join("%s" for _ in caller_org_ids)
        sql = f"""
            SELECT
                user_id, org_id, display_name, emails, identities,
                discoverable_aliases, visibility_policy, last_updated
            FROM user_directory
            WHERE user_id = %s
              AND org_id IN ({placeholders})
        """
        try:
            rows = await self._pool.fetch(sql, user_id, *caller_org_ids)
            return [self._row_to_entry(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("user_directory.get_by_user_id_failed: %s", exc)
            return []

    async def upsert(self, entry: DirectoryEntry) -> None:
        """Upsert a directory row (called by directory_indexer on profile updates)."""
        if self._pool is None:
            return
        import json  # noqa: PLC0415

        sql = """
            INSERT INTO user_directory
                (user_id, org_id, display_name, emails, identities,
                 discoverable_aliases, visibility_policy, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, org_id) DO UPDATE SET
                display_name         = EXCLUDED.display_name,
                emails               = EXCLUDED.emails,
                identities           = EXCLUDED.identities,
                discoverable_aliases = EXCLUDED.discoverable_aliases,
                visibility_policy    = EXCLUDED.visibility_policy,
                last_updated         = NOW()
        """
        try:
            await self._pool.execute(
                sql,
                entry.user_id,
                entry.org_id,
                entry.display_name,
                entry.emails,
                json.dumps(entry.identities),
                entry.discoverable_aliases,
                entry.visibility_policy,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("user_directory.upsert_failed: %s", exc)

    async def remove(self, user_id: str, org_id: str) -> None:
        """Remove a directory entry (called on membership removal, FR-AK-001)."""
        if self._pool is None:
            return
        sql = "DELETE FROM user_directory WHERE user_id = %s AND org_id = %s"
        try:
            await self._pool.execute(sql, user_id, org_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("user_directory.remove_failed: %s", exc)

    @staticmethod
    def _row_to_entry(row: Any) -> DirectoryEntry:
        import json as _json  # noqa: PLC0415

        identities = row["identities"]
        if isinstance(identities, str):
            try:
                identities = _json.loads(identities)
            except Exception:  # noqa: BLE001
                identities = {}

        return DirectoryEntry(
            user_id=row["user_id"],
            org_id=row["org_id"],
            display_name=row["display_name"] or "",
            emails=list(row["emails"] or []),
            identities=identities or {},
            discoverable_aliases=list(row["discoverable_aliases"] or []),
            visibility_policy=row["visibility_policy"] or "org_default",
            last_updated=row.get("last_updated"),
        )
