# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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

from graphclaw.inbound.models import CandidateNodeMatch, TaskResolution
from graphclaw.models.enums import ConfidenceLevel, MatchedBy

# ---------------------------------------------------------------------------
# Task ID regex
# ---------------------------------------------------------------------------

# Matches TSK-{INITIALS}-{4+digit sequence}-{TYPE_CODE} anywhere in text.
# TYPE_CODE is one of: DEL ATM FLW CMP APR MIL RVW REC DEC CHK RES
TASK_ID_REGEX: re.Pattern[str] = re.compile(
    r"\bTSK-[A-Z]{2,}-\d{4,}-(?:DEL|ATM|FLW|CMP|APR|MIL|RVW|REC|DEC|CHK|RES)\b"
)

_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "have",
    "about",
    "just",
    "into",
    "your",
    "please",
    "update",
    "status",
}


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

    def __init__(
        self,
        graph_repo: object | None = None,
        pool: object | None = None,
        embedding_client: object | None = None,
    ) -> None:
        self._repo = graph_repo
        self._pool = pool
        self._embedding_client = embedding_client

    async def resolve(
        self,
        message_text: str,
        subject: str = "",
        user_id: str | None = None,
    ) -> TaskResolution:
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
        if self._pool is not None and self._embedding_client is not None:
            vector_result = await self._vector_search(message_text, subject)
            if vector_result.task_id:
                return vector_result

            candidates = await self._suggest_candidates(message_text, subject, user_id)
            return TaskResolution(
                match_unavailable_reason="low_embedding_confidence",
                candidate_nodes=candidates,
            )

        if self._repo is not None:
            candidates = await self._suggest_candidates(message_text, subject, user_id)
            return TaskResolution(
                match_unavailable_reason="embedding_service_unavailable",
                candidate_nodes=candidates,
            )

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

        Builds an embedding text from the subject and the first 300 characters
        of the body, then queries the ``node_embeddings`` table using cosine
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
            Backward compatible: if no ``embedding_client`` was provided at
            construction, returns an unmatched ``TaskResolution``.
        """
        embedding_text = f"{subject or ''} {body[:300]}".strip()
        if not embedding_text:
            return TaskResolution()

        # Backward compatibility: skip vector search if no embedding client.
        if self._embedding_client is None:
            return TaskResolution()

        try:
            # Generate embedding for the search query.
            embedding_vector = await self._embedding_client.embed(embedding_text)  # type: ignore[union-attr]

            async with self._pool.connection() as conn:  # type: ignore[union-attr]
                row = await conn.fetchrow(
                    """
                    SELECT node_id, embedding,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM node_embeddings
                    ORDER BY embedding <=> %s::vector
                    LIMIT 1
                    """,
                    (embedding_vector, embedding_vector),
                )
                if row is None:
                    return TaskResolution()
                similarity: float = float(row["similarity"])
                task_id = row["node_id"]
                # Fetch task title for matched_text (fallback to task_id if unavailable).
                matched_text = task_id
                if self._repo is not None:
                    try:
                        node = await self._repo.get_node(task_id)  # type: ignore[union-attr]
                        if node:
                            matched_text = node.get("title", task_id)
                    except Exception:
                        pass

                if similarity >= self.HIGH_THRESHOLD:
                    return TaskResolution(
                        task_id=task_id,
                        matched_by=MatchedBy.VECTOR_SEARCH,
                        confidence=ConfidenceLevel.HIGH,
                        score=similarity,
                        matched_text=matched_text,
                    )
                if similarity >= self.MEDIUM_THRESHOLD:
                    return TaskResolution(
                        task_id=task_id,
                        matched_by=MatchedBy.VECTOR_SEARCH,
                        confidence=ConfidenceLevel.MEDIUM,
                        score=similarity,
                        matched_text=matched_text,
                    )
        except Exception:
            pass

        return TaskResolution()

    async def _suggest_candidates(
        self,
        body: str,
        subject: str,
        user_id: str | None,
    ) -> list[CandidateNodeMatch]:
        """Suggest likely task candidates for manual matching.

        Uses lightweight lexical overlap between inbound text and task
        title/description as a fail-open fallback when embedding search
        cannot produce a confident match.
        """
        if self._repo is None:
            return []

        tasks: list[dict] = []
        try:
            if user_id and hasattr(self._repo, "list_nodes_by_user"):
                tasks = await self._repo.list_nodes_by_user("TaskNode", user_id)  # type: ignore[union-attr]
            else:
                tasks = await self._repo.list_nodes("TaskNode")  # type: ignore[union-attr]
        except Exception:
            return []

        query_text = f"{subject} {body}".lower()
        tokens = {
            token for token in re.findall(r"[a-z0-9]{3,}", query_text) if token not in _STOP_WORDS
        }

        scored: list[CandidateNodeMatch] = []
        fallback: list[CandidateNodeMatch] = []
        for task in tasks:
            task_id = str(task.get("id", "")).strip()
            if not task_id:
                continue

            state = str(task.get("state", "")) or None
            if state in {"COMPLETE", "CANCELLED", "SNOOZED"}:
                continue

            title = str(task.get("title", task_id)).strip() or task_id
            description = str(task.get("description", "")).strip()
            haystack = f"{title} {description}".lower()

            overlap = len([token for token in tokens if token in haystack])
            score = (overlap / len(tokens)) if tokens else 0.0

            candidate = CandidateNodeMatch(
                node_id=task_id,
                title=title,
                node_type=str(task.get("node_type", "TaskNode")),
                state=state,
                score=round(score, 3),
            )
            if score > 0:
                scored.append(candidate)
            else:
                fallback.append(candidate)

        ranked = sorted(scored, key=lambda item: item.score, reverse=True)
        if ranked:
            return ranked[:5]
        return fallback[:5]
