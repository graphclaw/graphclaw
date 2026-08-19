# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.episodic_recall — Shared keyword+recency episodic memory search.

Description
-----------
Extracted from ``MainOrchestrator._tool_recall_episodic`` so the same
relevance-scoring logic backs both the orchestrator's ``recall_episodic``
tool and ``SubAgentRunner``'s system-prompt assembly. Before this module,
the sub-agent runtime injected ALL active episodic entries into its system
prompt against a hardcoded ``token_budget = 80_000`` — unscoped to the
delegated task, and able on its own to dwarf every other section of an
otherwise well-isolated 2-message sub-agent prompt (see
docs/planning/build-plan.md, Wave Model-Routing).

No LLM call is used: episodic entries are named with dates and labels, so
filename matching plus a light content scan is fast and sufficient.

Design Patterns
---------------
- Pure function over StorageClient: no class state, so both callers (an
  orchestrator method and a sub-agent runner method) can share it without
  either owning the other's lifecycle.

Public API
----------
- EpisodicMatch: one scored episodic memory match.
- recall_episodic: keyword+recency search over one agent's episodic memory.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, StoragePaths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.infra.storage import StorageClient

_KEYWORD_RE = re.compile(r"\W+")
_MIN_KEYWORD_LEN = 3
_FILENAME_EXACT_MATCH_SCORE = 10.0
_FILENAME_KEYWORD_SCORE = 5.0
_CONTENT_KEYWORD_SCORE = 3.0
_CONTENT_EXCERPT_CHARS = 500
_FILENAME_CANDIDATE_LIMIT = 5


@dataclass(frozen=True)
class EpisodicMatch:
    """One scored episodic memory entry.

    Attributes:
        name: File name (last path segment), e.g. ``2026-08-01-standup.md``.
        content: Full decoded file content.
        score: Relevance score — filename match weighted higher than a
            content keyword hit; ties break toward newer filenames.
    """

    name: str
    content: str
    score: float


def _extract_keywords(query: str) -> list[str]:
    return [w for w in _KEYWORD_RE.split(query.lower()) if len(w) >= _MIN_KEYWORD_LEN]


async def recall_episodic(
    storage: StorageClient,
    *,
    user_id: str,
    agent_id: str,
    query: str,
    limit: int = 3,
    max_chars: int | None = None,
) -> list[EpisodicMatch]:
    """Keyword+recency search over one agent's active episodic memory.

    Args:
        storage: StorageClient to list/read episodic memory objects.
        user_id: Whose episodic memory to search.
        agent_id: Which agent's episodic memory (orchestrator's own, or a
            sub-agent's).
        query: Free-text query — for a sub-agent, callers should pass the
            delegated instructions (and task_id) as the query, since that
            free text already is the relevance signal, at no extra cost.
        limit: Maximum number of matches to return.
        max_chars: Optional cap on cumulative content length across all
            returned matches (in addition to ``limit`` on count). Entries
            are added in score order until the cap would be exceeded; the
            entry that would exceed it is dropped, not truncated, so no
            match is returned partially cut off mid-content.

    Returns:
        Matches sorted by descending score (ties broken toward
        alphabetically-later filenames — episodic filenames are
        date-prefixed, so this favours newer entries).

    Note:
        Never raises: on any storage failure, returns an empty list rather
        than propagating — matches the "never break the chat turn" contract
        every memory-loading path in this codebase follows.
    """
    query = query.strip()
    if not query or storage is None:
        return []

    from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

    active_prefix = StoragePaths.agent_memory_episodic_prefix(user_id, agent_id)
    archive_prefix = StoragePaths.agent_memory_episodic_archive_prefix(user_id, agent_id)
    try:
        keys = await storage.list_objects(active_prefix)
    except Exception:  # noqa: BLE001
        return []

    # Stricter than `archive_prefix not in k`: that substring check can match
    # an archive key that merely CONTAINS the archive prefix elsewhere in its
    # path, not just ones actually under it.
    entries = [k for k in keys if k.endswith(".md") and not k.startswith(archive_prefix)]
    if not entries:
        return []

    keywords = _extract_keywords(query)

    # Stage 1 — score by filename (exact query match, keyword substrings).
    scored: list[tuple[float, str]] = []
    for key in entries:
        name_lower = key.rsplit("/", 1)[-1].lower()
        score = 0.0
        if query.lower() in name_lower:
            score += _FILENAME_EXACT_MATCH_SCORE
        for kw in keywords:
            if kw in name_lower:
                score += _FILENAME_KEYWORD_SCORE
        scored.append((score, key))

    # Stage 2 — read an excerpt of the top filename candidates and boost by
    # keyword hits in content. Bounded to a handful of reads regardless of
    # how many total episodic entries exist.
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top_candidates = [key for _, key in scored[:_FILENAME_CANDIDATE_LIMIT]]
    content_cache: dict[str, str] = {}
    boosts: dict[str, float] = {}
    for key in top_candidates:
        try:
            raw = await storage.read(key)
            excerpt = raw.decode(errors="replace")[:_CONTENT_EXCERPT_CHARS]
        except FileNotFoundError:
            excerpt = ""
        content_cache[key] = excerpt
        excerpt_lower = excerpt.lower()
        boosts[key] = sum(_CONTENT_KEYWORD_SCORE for kw in keywords if kw in excerpt_lower)

    rescored = [(score + boosts.get(key, 0.0), key) for score, key in scored]
    rescored.sort(key=lambda t: (t[0], t[1]), reverse=True)

    matches: list[EpisodicMatch] = []
    used_chars = 0
    for score, key in rescored:
        if len(matches) >= limit:
            break
        content = content_cache.get(key)
        if content is None:
            try:
                content = (await storage.read(key)).decode(errors="replace")
            except FileNotFoundError:
                continue
        if max_chars is not None and used_chars + len(content) > max_chars:
            continue
        matches.append(EpisodicMatch(name=key.rsplit("/", 1)[-1], content=content, score=score))
        used_chars += len(content)

    return matches


def dumps_matches(matches: list[EpisodicMatch]) -> list[dict[str, Any]]:
    """Convert matches to the plain-dict shape the ``recall_episodic`` tool returns."""
    return [{"name": m.name, "content": m.content, "score": m.score} for m in matches]
