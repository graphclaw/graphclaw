"""graphclaw.inbound.resolver — Task resolution pipeline for inbound messages.

Description
-----------
``TaskResolver`` implements a two-stage resolution pipeline that maps an
inbound message to a task in the GraphClaw property graph. Stage 1 uses a
compiled regex to extract task IDs (``TSK-XX-NNNN-XXX``) directly from the
message text. Stage 2 falls back to pgvector cosine-similarity search over
pre-computed task title embeddings when no explicit ID is found.

Design Patterns
---------------
- Strategy / Template Method: The two resolution strategies (ID extraction vs.
  vector search) are encapsulated in private methods called from a single public
  ``resolve`` entry point, making it straightforward to add new strategies.
- Dependency Injection: ``graph_repo`` (GraphRepository) and ``pool``
  (asyncpg pool) are injected rather than created internally, enabling clean
  unit testing without a live database.
- Graceful Degradation: Each resolution stage catches all exceptions and returns
  a low-confidence ``TaskResolution`` rather than propagating errors, ensuring
  the pipeline always produces a result.

Public API
----------
- TASK_ID_REGEX: Compiled pattern that matches ``TSK-XX-NNNN-TYPE`` task IDs.
- TaskResolver: Resolves inbound messages to tasks via ID lookup and vector search.
- TaskResolver.resolve: Run the full 6-step resolution pipeline (async).

Dependencies
------------
- re: Compiled regex for task ID pattern matching.
- graphclaw.inbound.models: TaskResolution.
- graphclaw.models.enums: MatchedBy, ConfidenceLevel.

Notes
-----
The vector search stub in ``_vector_search`` provides the SQL template and
confidence threshold logic. A real deployment must supply an embedding vector
for the query text (e.g. via the Anthropic Embeddings API or a local model)
and pass it as the ``$1`` parameter. The stub currently returns an unmatched
``TaskResolution`` because no embedding model is wired up by default.
"""
from __future__ import annotations

import re

from graphclaw.inbound.models import TaskResolution
from graphclaw.models.enums import ConfidenceLevel, MatchedBy

# ---------------------------------------------------------------------------
# Task ID regex
# ---------------------------------------------------------------------------

# Matches TSK-{INITIALS}-{4+digit sequence}-{TYPE_CODE} anywhere in text.
# TYPE_CODE is one of: DEL ATM FLW CMP APR MIL RVW REC DEC CHK RES
TASK_ID_REGEX: re.Pattern[str] = re.compile(
    r"\bTSK-[A-Z]{2,}-\d{4,}-(?:DEL|ATM|FLW|CMP|APR|MIL|RVW|REC|DEC|CHK|RES)\b"
)


class TaskResolver:
    """Resolves inbound messages to tasks via ID lookup and vector search.

    Resolution pipeline (6 steps):
    1. Scan message text for task ID patterns using TASK_ID_REGEX.
    2. If a task ID is found, verify the task exists in the graph database.
    3. Return a HIGH-confidence TaskResolution on confirmed ID match.
    4. If no ID match, build embedding text from subject + first 500 chars of body.
    5. Query pgvector for the nearest neighbour task embedding.
    6. Apply confidence thresholds (≥0.7 HIGH, ≥0.4 MEDIUM) and return result.

    Args:
        graph_repo:
            ``GraphRepository`` instance used to verify task existence.
            When ``None``, ID matches are accepted without DB verification
            at a slightly reduced score (0.95).
        pool:
            ``asyncpg`` connection pool used for vector similarity queries.
            When ``None``, vector search is skipped entirely.
    """

    HIGH_THRESHOLD: float = 0.7
    MEDIUM_THRESHOLD: float = 0.4

    def __init__(self, graph_repo: object | None = None, pool: object | None = None) -> None:
        self._repo = graph_repo
        self._pool = pool

    async def resolve(self, message_text: str, subject: str = "") -> TaskResolution:
        """Run the full resolution pipeline on *message_text* and *subject*.

        Args:
            message_text: Plain-text body of the inbound message.
            subject: Subject line or title of the message (optional).

        Returns:
            A ``TaskResolution`` describing the best match found. If no match
            is possible, returns a default ``TaskResolution`` with all fields
            at their zero/None defaults.
        """
        # Step 1: Regex scan for task IDs in body + subject combined.
        task_id = self._extract_task_id(message_text + " " + (subject or ""))

        if task_id:
            # Step 2: Optionally verify task exists in graph.
            if self._repo is not None:
                try:
                    node = await self._repo.get_node(task_id)  # type: ignore[union-attr]
                    if node:
                        # Step 3a: Confirmed match — score 1.0.
                        return TaskResolution(
                            task_id=task_id,
                            matched_by=MatchedBy.TASK_ID,
                            confidence=ConfidenceLevel.HIGH,
                            score=1.0,
                            matched_text=task_id,
                        )
                except Exception:
                    pass

            # Step 3b: ID match without DB confirmation — still HIGH confidence.
            return TaskResolution(
                task_id=task_id,
                matched_by=MatchedBy.TASK_ID,
                confidence=ConfidenceLevel.HIGH,
                score=0.95,
                matched_text=task_id,
            )

        # Steps 4-6: Vector search fallback when a pool is available.
        if self._pool is not None:
            return await self._vector_search(message_text, subject)

        # No match possible without a pool.
        return TaskResolution()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_task_id(self, text: str) -> str | None:
        """Return the first task ID found in *text*, or ``None``.

        Args:
            text: Combined message body and subject text to scan.

        Returns:
            The matched task ID string (e.g. ``"TSK-AB-1234-ATM"``), or
            ``None`` if no valid task ID is present.
        """
        match = TASK_ID_REGEX.search(text)
        return match.group(0) if match else None

    async def _vector_search(self, body: str, subject: str) -> TaskResolution:
        """Query pgvector for the nearest-neighbour task to *body* + *subject*.

        Builds an embedding text from the subject and the first 500 characters
        of the body, then queries the ``task_embeddings`` table using cosine
        similarity (``<=>`` operator). Confidence is assigned based on the
        similarity score against ``HIGH_THRESHOLD`` and ``MEDIUM_THRESHOLD``.

        Args:
            body: Plain-text message body.
            subject: Message subject / title.

        Returns:
            A ``TaskResolution`` with the best vector match, or an unmatched
            ``TaskResolution`` if the query fails or no result clears the
            MEDIUM threshold.

        Notes:
            This method provides the SQL template and threshold logic. A
            production deployment must supply the embedding vector for
            ``embedding_text`` (via an embedding API call) before passing it
            as ``$1``. Currently returns an unmatched result because no
            embedding model is wired up.
        """
        embedding_text = f"{subject or ''} {body[:500]}".strip()
        if not embedding_text:
            return TaskResolution()

        # Embedding SQL template (requires a real embedding vector as $1):
        # SELECT task_id, title,
        #        1 - (embedding <=> $1::vector) AS similarity
        # FROM task_embeddings
        # ORDER BY embedding <=> $1::vector
        # LIMIT 1
        #
        # Real implementation would call an embedding model, then:
        #   async with self._pool.connection() as conn:
        #       row = await conn.fetchrow(sql, embedding_vector)
        #
        # For now, attempt the query with a no-op and return unmatched.
        try:
            async with self._pool.connection() as conn:  # type: ignore[union-attr]
                row = await conn.fetchrow(
                    """
                    SELECT task_id, title,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM task_embeddings
                    ORDER BY embedding <=> $1::vector
                    LIMIT 1
                    """,
                    None,  # placeholder — real impl must supply embedding vector
                )
                if row is None:
                    return TaskResolution()
                similarity: float = float(row["similarity"])
                if similarity >= self.HIGH_THRESHOLD:
                    return TaskResolution(
                        task_id=row["task_id"],
                        matched_by=MatchedBy.VECTOR_SEARCH,
                        confidence=ConfidenceLevel.HIGH,
                        score=similarity,
                        matched_text=row["title"],
                    )
                if similarity >= self.MEDIUM_THRESHOLD:
                    return TaskResolution(
                        task_id=row["task_id"],
                        matched_by=MatchedBy.VECTOR_SEARCH,
                        confidence=ConfidenceLevel.MEDIUM,
                        score=similarity,
                        matched_text=row["title"],
                    )
        except Exception:
            pass

        return TaskResolution()
