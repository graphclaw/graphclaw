"""graphclaw.identity.embedding — Embedding utilities for user_directory semantic search (FR-DIR-001).

Description
-----------
Provides ``UserDirectoryEmbedder`` which generates text embeddings for
``user_directory`` rows so that semantic (pgvector cosine) searches complement
the trigram fuzzy search.

The embedding input is a concatenation of the user's canonical fields::

    "{display_name} | {emails_joined} | {discoverable_aliases_joined}"

This text is embedded via ``EmbeddingClient`` and stored in a ``vector(1536)``
column added by migration 0023.  The column is optional — if absent, semantic
search falls back gracefully to trigram-only results.

Design Patterns
---------------
- Service Object: ``UserDirectoryEmbedder`` has no state beyond its client;
  all operations are pure async functions.
- Graceful Degradation: embedding failures are logged and swallowed — the
  directory write always succeeds regardless of embedding availability.

Public API
----------
- UserDirectoryEmbedder: Generates and upserts embedding vectors for directory rows.
- build_embedding_text: Pure function that constructs the embedding input string.

Dependencies
------------
- graphclaw.infra.embeddings: EmbeddingClient.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_EMBEDDING_COLUMN_UPSERT_SQL = """
    UPDATE user_directory
       SET embedding = %s
     WHERE user_id = %s
       AND org_id = %s
"""


def build_embedding_text(
    display_name: str,
    emails: list[str],
    discoverable_aliases: list[str],
) -> str:
    """Build the canonical embedding input string for a directory row.

    Concatenates display_name, emails, and discoverable_aliases separated by
    pipe symbols so the embedding captures all searchable identifiers.

    Parameters
    ----------
    display_name:
        Canonical display name for the user.
    emails:
        Known email addresses.
    discoverable_aliases:
        Alias tokens surfaced in directory search.

    Returns
    -------
    str
        Combined text ready for embedding.
    """
    parts = [display_name]
    if emails:
        parts.append(" ".join(emails))
    if discoverable_aliases:
        parts.append(" ".join(discoverable_aliases))
    return " | ".join(p for p in parts if p)


class UserDirectoryEmbedder:
    """Generates and persists embedding vectors for user_directory rows (FR-DIR-001).

    Parameters
    ----------
    embedding_client:
        ``EmbeddingClient`` instance for vector generation.
    pool:
        Async DB pool (psycopg3-style) with ``execute(sql, args)`` method.
        When ``None``, embedding writes are silently skipped (test mode).
    """

    def __init__(self, embedding_client: Any, pool: Any | None = None) -> None:
        self._client = embedding_client
        self._pool = pool

    async def embed_and_store(
        self,
        user_id: str,
        org_id: str,
        display_name: str,
        emails: list[str],
        discoverable_aliases: list[str],
    ) -> None:
        """Generate an embedding vector for the row and write it to the DB.

        Silently swallows errors so directory upserts are never blocked by
        embedding failures.

        Parameters
        ----------
        user_id:
            Platform user ID.
        org_id:
            Org context for this row.
        display_name:
            Canonical name.
        emails:
            Known email addresses.
        discoverable_aliases:
            Searchable alias tokens.
        """
        if self._client is None:
            return
        try:
            text = build_embedding_text(display_name, emails, discoverable_aliases)
            vector: list[float] = await self._client.embed(text)
            await self._write_vector(user_id, org_id, vector)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "user_directory.embedding_failed user=%s org=%s: %s", user_id, org_id, exc
            )

    async def _write_vector(self, user_id: str, org_id: str, vector: list[float]) -> None:
        """Write the embedding vector to the ``user_directory`` table."""
        if self._pool is None:
            return
        try:
            # pgvector accepts a list/array; cast to string for portability
            vector_str = "[" + ",".join(str(v) for v in vector) + "]"
            await self._pool.execute(
                _EMBEDDING_COLUMN_UPSERT_SQL,
                vector_str,
                user_id,
                org_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Column may not exist if migration 0023 is not applied yet — degrade gracefully
            logger.debug("user_directory.embedding_write_failed (column may not exist): %s", exc)
