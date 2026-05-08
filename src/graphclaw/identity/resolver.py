# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.identity.resolver — resolve_user tool implementation (FR-ID-002).

Description
-----------
Returns ranked candidates matching a free-text query from:
  1. Local exact alias match (confidence 1.0)
  2. Local fuzzy name/alias match (confidence 0.7–0.9)
  3. Org directory row match (confidence 0.5–0.8)
  4. No match → empty list

Callers decide whether to create a new person (FR-ID-003) or pick an existing
candidate.

Design Patterns
---------------
- Strategy: Multiple resolution strategies applied in priority order.
- Result object: ``ResolutionCandidate`` is an immutable data class.

Public API
----------
- ResolutionCandidate: Ranked candidate returned by ``resolve_user``.
- UserResolver: Main resolver class.
- UserResolver.resolve(query, caller_context, hints): Return ranked candidates.

Dependencies
------------
- graphclaw.db.base: GraphStore
- graphclaw.cross_tenant.acl: CallerContext
"""

from __future__ import annotations

import json
import logging
from dataclasses import field
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class ResolutionCandidate(BaseModel):
    """A candidate returned by resolve_user (FR-ID-002)."""

    node_id: str
    source: Literal["local", "org_directory"]
    confidence: float
    reason: str
    display_name: str
    discriminators: dict = field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class UserResolver:
    """Multi-strategy user/resource identity resolver (FR-ID-002).

    Parameters
    ----------
    store:
        GraphStore (or duck-type with ``list_nodes``/``get_node`` async methods).
    directory_search:
        Optional callable ``(query, org_ids) -> list[dict]`` for org directory
        fuzzy search (FR-DIR-001).  When ``None``, org-directory step is skipped.
    """

    def __init__(
        self,
        store: Any,
        directory_search: Any | None = None,
    ) -> None:
        self._store = store
        self._directory_search = directory_search

    async def resolve(
        self,
        query: str,
        caller_user_id: str,
        caller_org_ids: list[str] | None = None,
        hints: dict | None = None,
        caller_context: Any | None = None,
    ) -> list[ResolutionCandidate]:
        """Return ranked candidates for *query*.

        Algorithm (FR-ID-002):
        1. Local exact alias hit → confidence 1.0.
        2. Local fuzzy name/alias hit → confidence proportional to ratio.
        3. Org directory match → confidence 0.5–0.8.

        Results are sorted by confidence descending; ties broken by source
        (local > org_directory).

        Parameters
        ----------
        query:
            Free-text name/alias to look up.
        caller_user_id:
            The user performing the lookup (scope enforcement).
        caller_org_ids:
            Org IDs the caller belongs to (for directory scoping, FR-DIR-002).
        hints:
            Optional hints like ``{channel: "telegram", value: "+44…"}`` to
            narrow the search.

        Returns
        -------
        list[ResolutionCandidate]
            Ranked candidates, highest confidence first.
        """
        candidates: list[ResolutionCandidate] = []
        query_lower = query.strip().lower()

        # Step 1 + 2: Local nodes (UserNode + ResourceNode)
        local_candidates = await self._search_local(
            query_lower, caller_user_id, hints, caller_context=caller_context
        )
        candidates.extend(local_candidates)

        # Step 3: Org directory (if available and org_ids provided)
        if self._directory_search and caller_org_ids:
            try:
                dir_candidates = await self._search_directory(
                    query_lower, caller_org_ids, seen_ids={c.node_id for c in candidates}
                )
                candidates.extend(dir_candidates)
            except Exception as exc:  # noqa: BLE001
                logger.warning("resolve_user.directory_search_failed: %s", exc)

        # Sort by confidence DESC, with local > org_directory tie-break
        candidates.sort(
            key=lambda c: (c.confidence, c.source == "local"),
            reverse=True,
        )
        return candidates

    async def _search_local(
        self,
        query_lower: str,
        caller_user_id: str,
        hints: dict | None,
        caller_context: Any | None = None,
    ) -> list[ResolutionCandidate]:
        """Search local UserNode + ResourceNode aliases and names."""
        results: list[ResolutionCandidate] = []

        for label in ("UserNode", "ResourceNode"):
            try:
                nodes = await self._store.list_nodes(label, caller_context=caller_context) or []
            except Exception as exc:  # noqa: BLE001
                logger.debug("resolve_user.list_nodes(%s) failed: %s", label, exc)
                continue

            for raw in nodes:
                node_id = _get(raw, "id") or _get(raw, "task_id") or ""
                if not node_id:
                    continue

                display_name = _get(raw, "name") or _get(raw, "display_name") or node_id

                # Exact alias match
                aliases = _get(raw, "aliases") or []
                alias_values = [_extract_alias_value(a) for a in aliases]
                if query_lower in alias_values:
                    results.append(
                        ResolutionCandidate(
                            node_id=node_id,
                            source="local",
                            confidence=1.0,
                            reason="exact_alias",
                            display_name=display_name,
                            discriminators=_extract_discriminators(raw),
                        )
                    )
                    continue

                # Exact name match
                if display_name.lower() == query_lower:
                    results.append(
                        ResolutionCandidate(
                            node_id=node_id,
                            source="local",
                            confidence=0.95,
                            reason="exact_name",
                            display_name=display_name,
                            discriminators=_extract_discriminators(raw),
                        )
                    )
                    continue

                # Fuzzy match
                ratio = SequenceMatcher(None, query_lower, display_name.lower()).ratio()
                if ratio >= 0.6:
                    results.append(
                        ResolutionCandidate(
                            node_id=node_id,
                            source="local",
                            confidence=round(ratio * 0.9, 3),
                            reason="fuzzy_name",
                            display_name=display_name,
                            discriminators=_extract_discriminators(raw),
                        )
                    )
                    continue

                # Alias fuzzy
                for alias_val in alias_values:
                    alias_ratio = SequenceMatcher(None, query_lower, alias_val).ratio()
                    if alias_ratio >= 0.7:
                        results.append(
                            ResolutionCandidate(
                                node_id=node_id,
                                source="local",
                                confidence=round(alias_ratio * 0.85, 3),
                                reason="fuzzy_alias",
                                display_name=display_name,
                                discriminators=_extract_discriminators(raw),
                            )
                        )
                        break

        return results

    async def _search_directory(
        self,
        query_lower: str,
        caller_org_ids: list[str],
        seen_ids: set[str],
    ) -> list[ResolutionCandidate]:
        """Search org directory via the provided callable."""
        rows = await self._directory_search(query_lower, caller_org_ids)
        results = []
        for row in rows or []:
            user_id = row.get("user_id", "")
            if not user_id or user_id in seen_ids:
                continue
            display_name = row.get("display_name", user_id)
            ratio = SequenceMatcher(None, query_lower, display_name.lower()).ratio()
            confidence = round(0.5 + ratio * 0.3, 3)  # 0.5–0.8
            results.append(
                ResolutionCandidate(
                    node_id=user_id,
                    source="org_directory",
                    confidence=confidence,
                    reason="directory_fuzzy",
                    display_name=display_name,
                    discriminators={"org_id": row.get("org_id", "")},
                )
            )
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_alias_value(a: Any) -> str:
    """Extract the canonical lowercase alias value from various storage formats.

    AGE stores nested dicts in lists as JSON strings, so we handle both
    native-dict and JSON-string representations.
    """
    if isinstance(a, dict):
        return a.get("value", "").lower()
    if isinstance(a, str):
        # Try JSON-string form: '{"value": "...", "source": "email", ...}'
        stripped = a.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed.get("value", "").lower()
            except (ValueError, TypeError):
                pass
        return stripped.lower()
    return str(a).lower()


def _extract_discriminators(raw: Any) -> dict:
    d: dict = {}
    for key in ("role", "workspace_id", "email", "contact"):
        val = _get(raw, key)
        if val:
            d[key] = val
    identities = _get(raw, "identities")
    if identities and isinstance(identities, dict):
        emails = identities.get("emails", [])
        if emails:
            d["email_domain"] = emails[0].split("@")[-1] if "@" in emails[0] else emails[0]
    return d
