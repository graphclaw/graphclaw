# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.a2a.key_manager — A2A API key lifecycle management.

Description
-----------
Provides ``A2AKeyManager``, which handles the full lifecycle of Agent-to-Agent
API keys: generation, registration, rotation, revocation, and constant-time
verification.  Keys are hashed with SHA-256 before storage; the plaintext key
is returned to the caller exactly once (at generation or rotation) and is never
stored in the graph or logs.

Each registered agent is backed by a ``ResourceNode`` in the property graph
with the following extra fields written via ``update_node``:

- ``api_key_hash``: SHA-256 hex digest of the plaintext key.  Present when
  the key is active; absent (or ``None``) when the key has been revoked.
- ``agent_name``: Human-readable agent label (also the node ``name`` field).
- ``user_id``: Platform user ID that owns this agent registration.
- ``description``: Optional free-text description.
- ``callback_url``: Optional HTTPS callback URL for future push notifications.

Design Patterns
---------------
- Dependency Injection: ``GraphStore`` is injected via the constructor so the
  class is easily testable with a mock store.
- Constant-time comparison: ``hmac.compare_digest`` prevents timing attacks
  during key verification.
- Single responsibility: Key cryptography concerns live here; HTTP concerns
  live in ``routes.py``; dependency wiring lives in ``middleware.py``.

Public API
----------
- A2AKeyManager: Main class for key lifecycle operations.
  - generate_key() -> tuple[str, str]
  - register_agent(user_id, registration) -> tuple[A2AKeyRef, str]
  - rotate_key(user_id, key_id) -> tuple[str, str]
  - revoke_key(user_id, key_id) -> None
  - verify_key(plaintext_key) -> str | None
  - list_agents(user_id) -> list[A2AKeyRef]

Dependencies
------------
- graphclaw.a2a.models: A2AKeyRef, A2ARegistration.
- graphclaw.db.base: GraphStore.
- graphclaw.models.base: generate_resource_id, utcnow.
- graphclaw.models.enums: ResourceType.
- hashlib, hmac, secrets: stdlib cryptography.
- logging, datetime: stdlib.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from graphclaw.a2a.models import A2AKeyRef, A2ARegistration
from graphclaw.models.base import generate_resource_id, utcnow

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

KEY_PREFIX = "wg_agent_"
# secrets.token_urlsafe(24) produces 32 URL-safe base64 characters
KEY_SUFFIX_BYTES = 24

_RESOURCE_LABEL = "ResourceNode"
_AGENT_RESOURCE_TYPE = "AI_AGENT"


# ── A2AKeyManager ──────────────────────────────────────────────────────────────


class A2AKeyManager:
    """API key lifecycle manager for A2A agent registrations.

    Parameters
    ----------
    graph_store:
        An initialised ``GraphStore`` instance.  Used to persist and query
        agent ``ResourceNode`` records in the property graph.

    Notes
    -----
    Construct via ``A2AKeyManager(graph_store)`` where ``graph_store`` is
    obtained from the gateway ``deps`` module or injected in tests.
    """

    def __init__(self, graph_store: GraphStore) -> None:
        self._store = graph_store

    # ── Key generation ─────────────────────────────────────────────────────────

    def generate_key(self) -> tuple[str, str]:
        """Generate a new A2A API key.

        The plaintext key follows the format ``wg_agent_<32 chars>`` where the
        suffix is drawn from ``secrets.token_urlsafe(24)`` (which yields exactly
        32 URL-safe base64 characters).

        Returns
        -------
        tuple[str, str]:
            ``(plaintext_key, sha256_hex_digest)``.  The plaintext key is shown
            to the caller once and MUST NOT be stored.  Only the hex digest is
            persisted in the graph.
        """
        suffix = secrets.token_urlsafe(KEY_SUFFIX_BYTES)
        plaintext = f"{KEY_PREFIX}{suffix}"
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        return plaintext, digest

    # ── Registration ───────────────────────────────────────────────────────────

    async def register_agent(
        self,
        user_id: str,
        registration: A2ARegistration,
    ) -> tuple[A2AKeyRef, str]:
        """Register a new agent and issue its first API key.

        Creates a ``ResourceNode`` in the property graph with the SHA-256 hash
        of the generated key.  The plaintext key is returned to the caller and
        never stored.

        Parameters
        ----------
        user_id:
            Platform user ID (``USER-{uuid}``) of the registering user.
        registration:
            ``A2ARegistration`` payload from the HTTP request.

        Returns
        -------
        tuple[A2AKeyRef, str]:
            ``(key_ref, plaintext_key)``.  ``plaintext_key`` is shown once;
            ``key_ref`` contains all metadata needed for subsequent operations.
        """
        plaintext, key_hash = self.generate_key()
        node_id = generate_resource_id()
        now = utcnow()

        node_data: dict[str, Any] = {
            "id": node_id,
            "resource_type": _AGENT_RESOURCE_TYPE,
            "name": registration.agent_name,
            "contact": registration.callback_url,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "version": 0,
            # A2A-specific extension fields
            "api_key_hash": key_hash,
            "user_id": user_id,
            "description": registration.description,
            "callback_url": registration.callback_url,
            "agent_name": registration.agent_name,
        }

        await self._store.create_node(node_data)

        key_ref = A2AKeyRef(
            key_id=node_id,
            agent_name=registration.agent_name,
            user_id=user_id,
            created_at=now,
            resource_node_id=node_id,
        )

        logger.info(
            "A2AKeyManager: registered agent key_id=%s agent_name=%s user_id=%s",
            node_id,
            registration.agent_name,
            user_id,
        )
        return key_ref, plaintext

    # ── Key rotation ───────────────────────────────────────────────────────────

    async def rotate_key(self, user_id: str, key_id: str) -> tuple[str, str]:
        """Rotate the API key for an existing agent registration.

        Generates a new key, updates the ``api_key_hash`` on the backing
        ``ResourceNode``, and returns the new plaintext key.  The old key is
        immediately invalidated.

        Parameters
        ----------
        user_id:
            Platform user ID of the key owner.  Used to verify ownership.
        key_id:
            Resource node ID of the agent to rotate (returned from registration).

        Returns
        -------
        tuple[str, str]:
            ``(new_plaintext_key, new_sha256_hex_digest)``.

        Raises
        ------
        KeyError:
            If the node does not exist or does not belong to *user_id*.
        """
        node = await self._store.get_node(key_id)
        if node is None or node.get("user_id") != user_id:
            raise KeyError(f"Agent key_id={key_id!r} not found for user_id={user_id!r}")

        new_plaintext, new_hash = self.generate_key()
        await self._store.update_node(
            key_id,
            {
                "api_key_hash": new_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info("A2AKeyManager: rotated key for key_id=%s user_id=%s", key_id, user_id)
        return new_plaintext, new_hash

    # ── Key revocation ─────────────────────────────────────────────────────────

    async def revoke_key(self, user_id: str, key_id: str) -> None:
        """Revoke an agent API key by clearing its hash from the graph.

        Sets ``api_key_hash`` to ``None`` on the backing ``ResourceNode``.
        The node itself is retained (agent registration persists) but the key
        can no longer authenticate any requests.

        Parameters
        ----------
        user_id:
            Platform user ID of the key owner.  Used to verify ownership.
        key_id:
            Resource node ID of the agent whose key should be revoked.

        Raises
        ------
        KeyError:
            If the node does not exist or does not belong to *user_id*.
        """
        node = await self._store.get_node(key_id)
        if node is None or node.get("user_id") != user_id:
            raise KeyError(f"Agent key_id={key_id!r} not found for user_id={user_id!r}")

        await self._store.update_node(
            key_id,
            {
                "api_key_hash": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info("A2AKeyManager: revoked key for key_id=%s user_id=%s", key_id, user_id)

    # ── Key verification ───────────────────────────────────────────────────────

    async def verify_key(self, plaintext_key: str) -> str | None:
        """Verify an incoming API key and return the associated user_id.

        Computes the SHA-256 hash of *plaintext_key* and searches for a
        matching ``ResourceNode`` with a non-null ``api_key_hash`` field.
        The final comparison uses ``hmac.compare_digest`` to prevent timing
        attacks.

        Parameters
        ----------
        plaintext_key:
            The raw key value extracted from the ``X-Agent-Api-Key`` header.

        Returns
        -------
        str | None:
            The ``user_id`` of the key owner if the key is valid and active.
            ``None`` if the key does not match any registered agent or has
            been revoked.
        """
        if not plaintext_key or not plaintext_key.startswith(KEY_PREFIX):
            return None

        incoming_hash = hashlib.sha256(plaintext_key.encode()).hexdigest()

        # List all ResourceNodes that have an api_key_hash set.
        # In production with a real AGE backend this would be a Cypher index
        # lookup by hash; for now we filter in Python after list_nodes.
        try:
            nodes: list[dict[str, Any]] = await self._store.list_nodes(
                _RESOURCE_LABEL, {"resource_type": _AGENT_RESOURCE_TYPE}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("A2AKeyManager.verify_key: graph query failed: %s", exc)
            return None

        for node in nodes:
            stored_hash = node.get("api_key_hash")
            if not stored_hash:
                continue
            # Constant-time comparison — both sides must be the same type/length
            # for hmac.compare_digest; encode to bytes for safety.
            try:
                match = hmac.compare_digest(
                    incoming_hash.encode("ascii"),
                    stored_hash.encode("ascii"),
                )
            except Exception:  # noqa: BLE001
                continue
            if match:
                user_id: str | None = node.get("user_id")
                logger.debug(
                    "A2AKeyManager.verify_key: matched key_id=%s user_id=%s",
                    node.get("id"),
                    user_id,
                )
                return user_id

        return None

    # ── Agent listing ──────────────────────────────────────────────────────────

    async def list_agents(self, user_id: str) -> list[A2AKeyRef]:
        """Return all active agent registrations for *user_id*.

        Only nodes with a non-null ``api_key_hash`` are included (revoked
        agents are omitted).

        Parameters
        ----------
        user_id:
            Platform user ID to list agents for.

        Returns
        -------
        list[A2AKeyRef]:
            Sorted (by ``created_at`` ascending) list of key references.
            Empty list if the user has no registered agents.
        """
        try:
            nodes: list[dict[str, Any]] = await self._store.list_nodes(
                _RESOURCE_LABEL,
                {"user_id": user_id, "resource_type": _AGENT_RESOURCE_TYPE},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "A2AKeyManager.list_agents: graph query failed for user_id=%s: %s",
                user_id,
                exc,
            )
            return []

        refs: list[A2AKeyRef] = []
        for node in nodes:
            if not node.get("api_key_hash"):
                continue  # Skip revoked agents
            node_id = node.get("id", "")
            agent_name = node.get("agent_name") or node.get("name", "")
            created_raw = node.get("created_at")
            if isinstance(created_raw, str):
                try:
                    created_at = datetime.fromisoformat(created_raw)
                except ValueError:
                    created_at = utcnow()
            elif isinstance(created_raw, datetime):
                created_at = created_raw
            else:
                created_at = utcnow()

            refs.append(
                A2AKeyRef(
                    key_id=node_id,
                    agent_name=agent_name,
                    user_id=user_id,
                    created_at=created_at,
                    resource_node_id=node_id,
                )
            )

        refs.sort(key=lambda r: r.created_at)
        return refs
