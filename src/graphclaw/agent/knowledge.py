# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.knowledge — KnowledgeBase: system domain knowledge from MinIO.

Description
-----------
Provides ``KnowledgeBase``, which loads Markdown domain-knowledge documents from
``system/knowledge/`` in object storage and serves them to the agent on demand via
the ``read_knowledge`` tool.  Documents are cached in-memory for the lifetime of
the ``KnowledgeBase`` instance (typically one session).

The knowledge base encodes graph construction rules, state machine rules, edge
creation guidance, goal inference patterns, scoring context, and follow-up timing
that would otherwise be absent from the agent's reasoning.

Seeded Topics
-------------
- node_creation_rules   — when to use each of the 11 task types
- edge_creation_rules   — when to use each of the 15 edge types
- state_machine_rules   — valid state transitions and transition guards
- goal_inference_rules  — bottom-up intent extraction + retrieval strategy
- scoring_context       — W1-W7 factor meanings and priority logic
- follow_up_timing      — urgency escalation rules and cadence by domain

Public API
----------
- KnowledgeBase: Reads and caches domain knowledge documents.
- KnowledgeBase.get_index: Compact topic list for the system prompt.
- KnowledgeBase.read: Load one topic document (with in-session caching).
- KnowledgeBase.list_topics: Enumerate available topics from storage.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, StoragePaths.
"""

from __future__ import annotations

import logging

from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)

# Canonical topics — used for validation and the compact index.
KNOWN_TOPICS = [
    "node_creation_rules",
    "edge_creation_rules",
    "state_machine_rules",
    "goal_inference_rules",
    "scoring_context",
    "follow_up_timing",
]


class KnowledgeBase:
    """Reads and caches system domain knowledge documents from object storage.

    Parameters
    ----------
    storage_client:
        A concrete ``StorageClient`` implementation for MinIO/S3 reads.
    """

    def __init__(self, storage_client: StorageClient) -> None:
        self._storage = storage_client
        self._cache: dict[str, str] = {}
        self._topics: list[str] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_index(self) -> str:
        """Return a compact (~3-line) topic index for injection into the system prompt.

        The index tells the agent what topics are available so it can call
        ``read_knowledge(topic)`` before making graph construction decisions.
        """
        topics = await self.list_topics()
        if not topics:
            return ""
        topic_list = " | ".join(topics)
        return (
            "## Knowledge Base\n"
            "Call read_knowledge(topic) to load domain rules before creating nodes/edges.\n"
            f"Available topics: {topic_list}"
        )

    async def read(self, topic: str) -> str:
        """Load and return the domain knowledge document for *topic*.

        Parameters
        ----------
        topic:
            One of the seeded knowledge topic names (e.g. ``"node_creation_rules"``).

        Returns
        -------
        str
            The Markdown content of the document, or an error message if not found.
        """
        if topic in self._cache:
            return self._cache[topic]

        path = StoragePaths.system_knowledge(topic)
        try:
            raw = await self._storage.read(path)
            content = raw.decode()
            self._cache[topic] = content
            logger.debug("knowledge.read", extra={"topic": topic, "bytes": len(raw)})
            return content
        except FileNotFoundError:
            logger.warning("knowledge.read.not_found", extra={"topic": topic})
            return (
                f"Knowledge topic '{topic}' not found in storage. "
                f"Available topics: {', '.join(KNOWN_TOPICS)}"
            )
        except Exception as exc:
            logger.warning("knowledge.read.error", extra={"topic": topic, "error": str(exc)})
            return f"Failed to load knowledge topic '{topic}': {exc}"

    async def list_topics(self) -> list[str]:
        """Return the list of available knowledge topic names from storage.

        Result is cached for the lifetime of this instance (topics only change
        on deployment, and the orchestrator is a singleton).

        Falls back to ``KNOWN_TOPICS`` if the storage prefix cannot be listed.
        """
        if self._topics is not None:
            return self._topics

        prefix = StoragePaths.system_knowledge_prefix()
        try:
            keys = await self._storage.list_objects(prefix)
            topics = []
            for key in keys:
                if key.endswith(".md"):
                    # Extract topic name: system/knowledge/{topic}.md
                    name = key[len(prefix) :]
                    if name.endswith(".md"):
                        name = name[:-3]
                    if name:
                        topics.append(name)
            self._topics = sorted(topics) if topics else list(KNOWN_TOPICS)
        except Exception as exc:
            logger.warning("knowledge.list_topics.failed", extra={"error": str(exc)})
            self._topics = list(KNOWN_TOPICS)

        return self._topics


__all__ = ["KnowledgeBase", "KNOWN_TOPICS"]
