"""graphclaw.auth.provisioning — User Onboarding Provisioning service.

Description
-----------
Provides ``UserProvisioningService``, which atomically provisions all
resources required when a new user signs up via OAuth or any other
onboarding path.  Provisioning covers:

1. Creating a ``UserNode`` in the property graph.
2. Creating an S3 prefix marker (``users/{user_id}/.keep``) so the user's
   storage partition exists before any assets are written.
3. Creating a default ``WorkspaceNode`` linked to the user via an ``OWNS``
   edge in the graph.
4. Issuing initial RS256 access and refresh tokens.

If any step fails after prior steps have completed, the service rolls back
all completed steps in reverse order so the system is never left in a
partially-provisioned state.

The service is also idempotent: if a user with the given ``oauth_subject``
(matched by email) already exists, it returns the existing user's data with
``is_new_user=False`` and no side effects.

Design Patterns
---------------
- Rollback List: ``_rollback_steps`` accumulates async callables.  On
  exception, they are awaited in reverse order (LIFO) to undo committed work.
- Dataclass Result: ``ProvisioningResult`` is a plain dataclass so callers
  get a typed value object without Pydantic overhead at the service boundary.
- Dependency Injection: All infrastructure clients are injected via
  ``__init__`` so the service is fully testable without touching real
  infrastructure.

Public API
----------
- ProvisioningResult: Dataclass returned by ``provision_new_user``.
- UserProvisioningService: Main onboarding orchestration class.
  - provision_new_user(oauth_subject, email, display_name, provider, org_id) -> ProvisioningResult
  - deprovision_user(user_id) -> None

Dependencies
------------
- graphclaw.db.base: GraphStore ABC.
- graphclaw.infra.storage: StorageClient ABC.
- graphclaw.auth.jwt: JWTService.
- graphclaw.models.nodes: UserNode, WorkspaceNode.
- graphclaw.models.base: generate_user_id, generate_workspace_id, utcnow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Coroutine, Any

from graphclaw.auth.jwt import JWTService
from graphclaw.db.base import GraphStore
from graphclaw.infra.storage import StorageClient
from graphclaw.models.base import generate_user_id, generate_workspace_id, utcnow
from graphclaw.models.nodes import UserNode, WorkspaceNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProvisioningResult:
    """Returned by ``UserProvisioningService.provision_new_user``.

    Attributes
    ----------
    user_id:
        The ``USER-{uuid}`` identifier for the provisioned (or existing) user.
    workspace_id:
        The ``WS-{uuid}`` identifier for the user's default workspace.
    access_token:
        Short-lived RS256 access token for immediate API use.
    refresh_token:
        Long-lived RS256 refresh token.
    is_new_user:
        ``True`` when a new user was created; ``False`` when the call was
        idempotent and returned an existing user.
    """

    user_id: str
    workspace_id: str
    access_token: str
    refresh_token: str
    is_new_user: bool


# ---------------------------------------------------------------------------
# Provisioning service
# ---------------------------------------------------------------------------


class UserProvisioningService:
    """Atomically provisions all resources for a new user.

    Parameters
    ----------
    graph_store:
        Graph backend used to create/delete ``UserNode`` and ``WorkspaceNode``
        vertices and the ``OWNS`` edge between them.
    storage_client:
        Object-storage backend used to create the user's S3 prefix marker.
    jwt_service:
        JWT service used to issue access and refresh tokens after provisioning.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        storage_client: StorageClient,
        jwt_service: JWTService,
    ) -> None:
        self._graph = graph_store
        self._storage = storage_client
        self._jwt = jwt_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def provision_new_user(
        self,
        oauth_subject: str,
        email: str,
        display_name: str,
        provider: str,
        org_id: str | None = None,
    ) -> ProvisioningResult:
        """Provision all resources for a new user, or return existing user data.

        Performs the following steps atomically (with rollback on failure):

        1. Idempotency check — if a user already exists for *email*, return
           immediately with ``is_new_user=False``.
        2. Create ``UserNode`` in the property graph.
        3. Write ``users/{user_id}/.keep`` placeholder to object storage.
        4. Create default ``WorkspaceNode`` in the property graph.
        5. Create ``OWNS`` edge from ``UserNode`` to ``WorkspaceNode``.
        6. Issue access and refresh tokens.

        Parameters
        ----------
        oauth_subject:
            Fully-qualified OAuth subject (e.g. ``"google:1234567890"``).
        email:
            User's email address.  Also used for idempotency lookup.
        display_name:
            Human-readable name (stored as ``UserNode.name``).
        provider:
            OAuth provider name: ``"google"``, ``"github"``, or
            ``"microsoft"``.
        org_id:
            Optional ``ORG-{uuid}`` to associate the new workspace with.
            When ``None``, an org association is omitted (solo workspace).

        Returns
        -------
        ProvisioningResult
            Populated result with tokens and IDs.

        Raises
        ------
        RuntimeError
            If provisioning fails and rollback also fails.  The original
            exception is chained via ``raise ... from``.
        """
        # ------------------------------------------------------------------
        # Step 0: idempotency check
        # ------------------------------------------------------------------
        existing = await self._find_user_by_email(email)
        if existing is not None:
            existing_user_id: str = existing["id"]
            existing_workspace_id = await self._find_default_workspace(existing_user_id)
            logger.info(
                "provision_new_user: user already exists",
                extra={"user_id": existing_user_id, "email": email},
            )
            return ProvisioningResult(
                user_id=existing_user_id,
                workspace_id=existing_workspace_id or "",
                access_token=self._jwt.issue_access_token(existing_user_id),
                refresh_token=self._jwt.issue_refresh_token(existing_user_id),
                is_new_user=False,
            )

        # ------------------------------------------------------------------
        # Rollback accumulator
        # ------------------------------------------------------------------
        _rollback_steps: list[Callable[[], Coroutine[Any, Any, None]]] = []

        user_id = generate_user_id()
        workspace_id = generate_workspace_id()
        now = utcnow()

        try:
            # --------------------------------------------------------------
            # Step 1: Create UserNode
            # --------------------------------------------------------------
            user_node = UserNode(
                id=user_id,
                name=display_name,
                email=email,
                role=provider,
                created_at=now,
                updated_at=now,
                version=0,
            )
            await self._graph.create_node(user_node)
            logger.debug("provision_new_user: UserNode created", extra={"user_id": user_id})

            async def _delete_user_node() -> None:
                await self._graph.delete_node(user_id)

            _rollback_steps.append(_delete_user_node)

            # --------------------------------------------------------------
            # Step 2: Create S3 prefix marker
            # --------------------------------------------------------------
            keep_key = f"users/{user_id}/.keep"
            await self._storage.write(keep_key, b"")
            logger.debug(
                "provision_new_user: S3 prefix created",
                extra={"key": keep_key},
            )

            async def _delete_s3_prefix() -> None:
                await self._storage.delete(keep_key)

            _rollback_steps.append(_delete_s3_prefix)

            # --------------------------------------------------------------
            # Step 3: Create default WorkspaceNode
            # --------------------------------------------------------------
            # WorkspaceNode.org_id is required; use a sentinel ORG ID when
            # no org is provided so the field constraint is satisfied.
            effective_org_id = org_id if org_id else f"ORG-{user_id.removeprefix('USER-')}"
            workspace_node = WorkspaceNode(
                id=workspace_id,
                org_id=effective_org_id,
                name="Default Workspace",
                description=f"Default workspace for {display_name}",
                is_default=True,
                member_ids=[user_id],
                created_at=now,
                updated_at=now,
                version=0,
            )
            await self._graph.create_node(workspace_node)
            logger.debug(
                "provision_new_user: WorkspaceNode created",
                extra={"workspace_id": workspace_id},
            )

            async def _delete_workspace_node() -> None:
                await self._graph.delete_node(workspace_id)

            _rollback_steps.append(_delete_workspace_node)

            # --------------------------------------------------------------
            # Step 4: Create OWNS edge (UserNode -> WorkspaceNode)
            # --------------------------------------------------------------
            await self._graph.create_edge(
                source_id=user_id,
                target_id=workspace_id,
                edge_type="OWNS",
                properties={"created_at": now.isoformat(), "is_default": True},
            )
            logger.debug(
                "provision_new_user: OWNS edge created",
                extra={"user_id": user_id, "workspace_id": workspace_id},
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "provision_new_user: step failed — rolling back",
                exc_info=True,
                extra={"user_id": user_id},
            )
            await self._run_rollback(_rollback_steps)
            raise RuntimeError(
                f"User provisioning failed for email={email!r}: {exc}"
            ) from exc

        # ------------------------------------------------------------------
        # Step 5: Issue tokens (no rollback needed — stateless JWTs)
        # ------------------------------------------------------------------
        access_token = self._jwt.issue_access_token(user_id)
        refresh_token = self._jwt.issue_refresh_token(user_id)

        logger.info(
            "provision_new_user: provisioning complete",
            extra={"user_id": user_id, "workspace_id": workspace_id},
        )
        return ProvisioningResult(
            user_id=user_id,
            workspace_id=workspace_id,
            access_token=access_token,
            refresh_token=refresh_token,
            is_new_user=True,
        )

    async def deprovision_user(self, user_id: str) -> None:
        """Remove all provisioned resources for *user_id*.

        Deletes (in order):

        1. S3 prefix marker ``users/{user_id}/.keep``.
        2. All workspace nodes owned by the user (via ``OWNS`` out-edges).
        3. The ``UserNode`` itself.

        Errors during individual cleanup steps are logged but do not abort
        the remaining steps, so as much cleanup as possible is always attempted.

        Parameters
        ----------
        user_id:
            The ``USER-{uuid}`` identifier to deprovision.
        """
        logger.info("deprovision_user: starting", extra={"user_id": user_id})

        # Step 1: Remove S3 prefix marker
        keep_key = f"users/{user_id}/.keep"
        try:
            await self._storage.delete(keep_key)
            logger.debug("deprovision_user: S3 prefix removed", extra={"key": keep_key})
        except Exception:  # noqa: BLE001
            logger.warning(
                "deprovision_user: failed to delete S3 prefix — continuing",
                exc_info=True,
                extra={"key": keep_key},
            )

        # Step 2: Remove workspace nodes owned by user
        try:
            owned_edges = await self._graph.get_edges(
                node_id=user_id, direction="out", edge_type="OWNS"
            )
            for edge in owned_edges:
                workspace_node_id: str | None = edge.get("target_id") or edge.get("to")
                if workspace_node_id:
                    try:
                        await self._graph.delete_node(workspace_node_id)
                        logger.debug(
                            "deprovision_user: workspace node deleted",
                            extra={"workspace_id": workspace_node_id},
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "deprovision_user: failed to delete workspace node — continuing",
                            exc_info=True,
                            extra={"workspace_id": workspace_node_id},
                        )
        except Exception:  # noqa: BLE001
            logger.warning(
                "deprovision_user: failed to fetch OWNS edges — continuing",
                exc_info=True,
                extra={"user_id": user_id},
            )

        # Step 3: Remove UserNode
        try:
            await self._graph.delete_node(user_id)
            logger.info("deprovision_user: UserNode deleted", extra={"user_id": user_id})
        except Exception:  # noqa: BLE001
            logger.error(
                "deprovision_user: failed to delete UserNode",
                exc_info=True,
                extra={"user_id": user_id},
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _find_user_by_email(self, email: str) -> dict | None:
        """Return the raw graph dict for the user with *email*, or None."""
        results = await self._graph.list_nodes("UserNode", {"email": email})
        if results:
            return results[0]
        return None

    async def _find_default_workspace(self, user_id: str) -> str | None:
        """Return the workspace_id of the user's default workspace, or None."""
        try:
            edges = await self._graph.get_edges(
                node_id=user_id, direction="out", edge_type="OWNS"
            )
            for edge in edges:
                props = edge.get("properties") or {}
                if props.get("is_default"):
                    return edge.get("target_id") or edge.get("to")
            # Fallback: return first owned workspace if none flagged as default
            if edges:
                return edges[0].get("target_id") or edges[0].get("to")
        except Exception:  # noqa: BLE001
            logger.warning(
                "_find_default_workspace: failed to fetch edges",
                exc_info=True,
                extra={"user_id": user_id},
            )
        return None

    @staticmethod
    async def _run_rollback(
        steps: list[Callable[[], Coroutine[Any, Any, None]]]
    ) -> None:
        """Execute rollback callables in reverse (LIFO) order.

        Each step is awaited independently.  Errors are logged but do not
        prevent subsequent rollback steps from running.
        """
        for rollback_fn in reversed(steps):
            try:
                await rollback_fn()
            except Exception:  # noqa: BLE001
                logger.error(
                    "provision_new_user: rollback step failed — continuing",
                    exc_info=True,
                )
