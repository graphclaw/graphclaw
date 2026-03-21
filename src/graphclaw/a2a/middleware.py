"""graphclaw.a2a.middleware — FastAPI dependency injection for A2A authentication.

Description
-----------
Provides FastAPI ``Depends``-compatible callables for A2A key management and
key-based authentication.  The ``A2AKeyManager`` singleton is initialised lazily
on first use and cached for the application lifetime.

Two public dependencies are exposed:

- ``get_a2a_key_manager`` — Returns the singleton ``A2AKeyManager``.  Used by
  all ``/api/v1/a2a/agents`` management endpoints.
- ``require_a2a_auth`` — Extracts and verifies the ``X-Agent-Api-Key`` header,
  returning the authenticated ``user_id``.  Used by ``POST /api/v1/task-update``.

Design Patterns
---------------
- Singleton with lazy init: ``_key_manager`` is set once on first access and
  reused across all requests.  Tests can inject a mock via
  ``app.dependency_overrides[get_a2a_key_manager] = lambda: mock_km``.
- Header extraction: ``APIKeyHeader`` is used instead of ``HTTPBearer`` because
  A2A authentication uses a custom ``X-Agent-Api-Key`` header rather than the
  standard ``Authorization: Bearer`` scheme.

Public API
----------
- get_a2a_key_manager: FastAPI dependency — returns singleton A2AKeyManager.
- require_a2a_auth: FastAPI dependency — returns authenticated user_id str.
- init_a2a_key_manager: Pre-warm or replace the singleton (startup / tests).
- reset_a2a_key_manager: Clear the singleton (tests).

Dependencies
------------
- graphclaw.a2a.key_manager: A2AKeyManager.
- graphclaw.db.factory: create_graph_store.
- fastapi: Depends, HTTPException, status (third-party).
- fastapi.security: APIKeyHeader (third-party).
- logging, os: stdlib.
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from graphclaw.a2a.key_manager import A2AKeyManager

logger = logging.getLogger(__name__)

# ── A2A API key header scheme ──────────────────────────────────────────────────
# ``auto_error=False`` so we can return a proper 403 (not 422) when the header
# is missing, matching the PRD behaviour for unauthenticated A2A requests.
_api_key_header = APIKeyHeader(name="X-Agent-Api-Key", auto_error=False)

# ── Module-level singleton ─────────────────────────────────────────────────────

_key_manager: A2AKeyManager | None = None


# ── Singleton management ───────────────────────────────────────────────────────


def get_a2a_key_manager() -> A2AKeyManager:
    """FastAPI dependency that returns the singleton ``A2AKeyManager``.

    Initialises the manager from environment variables on the first call.
    Subsequent calls return the cached instance.

    Returns
    -------
    A2AKeyManager:
        The application-wide A2A key manager instance.

    Notes
    -----
    The graph store is created via ``create_graph_store()`` from
    ``graphclaw.db.factory``, which reads ``GRAPH_DB_*`` environment variables.
    For local dev these default to the ``age`` backend (Postgres + Apache AGE).
    """
    global _key_manager  # noqa: PLW0603
    if _key_manager is None:
        logger.debug("A2AKeyManager singleton: initialising from environment")
        from graphclaw.db.factory import create_graph_store  # noqa: PLC0415

        graph_store = create_graph_store()
        _key_manager = A2AKeyManager(graph_store=graph_store)
    return _key_manager


def init_a2a_key_manager(manager: A2AKeyManager | None = None) -> None:
    """Pre-warm (or replace) the A2A key manager singleton.

    Parameters
    ----------
    manager:
        A pre-built ``A2AKeyManager`` instance to use as the singleton.
        When ``None``, the singleton is built from environment variables on
        the next call to ``get_a2a_key_manager()``.

    Notes
    -----
    Call this during application startup to ensure the graph store connection
    is established before the first request arrives.  Also useful in tests to
    inject a mock ``A2AKeyManager``.
    """
    global _key_manager  # noqa: PLW0603
    _key_manager = manager
    if manager is not None:
        logger.debug("A2AKeyManager singleton: pre-warmed with provided instance")
    else:
        logger.debug("A2AKeyManager singleton: cleared (will init on next access)")


def reset_a2a_key_manager() -> None:
    """Clear the singleton — primarily for use in tests.

    After calling this, the next call to ``get_a2a_key_manager()`` will
    re-initialise from environment variables.
    """
    global _key_manager  # noqa: PLW0603
    _key_manager = None


# ── FastAPI dependency callables ───────────────────────────────────────────────


async def require_a2a_auth(
    api_key: str | None = Depends(_api_key_header),
    key_manager: A2AKeyManager = Depends(get_a2a_key_manager),
) -> str:
    """Extract and verify the ``X-Agent-Api-Key`` header.

    Performs a constant-time hash comparison against stored agent keys in the
    graph.  Returns the authenticated ``user_id`` on success.

    Parameters
    ----------
    api_key:
        Value of the ``X-Agent-Api-Key`` request header.  ``None`` if the
        header is absent.
    key_manager:
        Singleton ``A2AKeyManager`` injected via ``Depends``.

    Returns
    -------
    str:
        The ``user_id`` of the agent owner if the key is valid.

    Raises
    ------
    HTTPException(403):
        If the header is missing, the key is invalid, or the key has been
        revoked.  Always 403 (not 401) to avoid disclosing whether an agent
        exists.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Agent-Api-Key header",
        )

    user_id = await key_manager.verify_key(api_key)

    if user_id is None:
        logger.warning(
            "require_a2a_auth: invalid or revoked A2A key (prefix=%s...)",
            api_key[:16] if len(api_key) >= 16 else api_key,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API key",
        )

    return user_id
