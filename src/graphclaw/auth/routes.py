# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.auth.routes — FastAPI router for OAuth 2.0 + JWT authentication.

Description
-----------
Provides the ``/auth`` route group implementing the full authentication flow:

1. ``GET  /auth/login``     — Redirect the browser to the IdP authorization page.
2. ``GET  /auth/callback``  — Receive the IdP callback, exchange the code for
                               tokens, look up or create a UserNode (stubbed),
                               and return GraphClaw access + refresh tokens.
3. ``POST /auth/refresh``   — Rotate a refresh token; return new token pair.
4. ``POST /auth/logout``    — Revoke a refresh token.
5. ``GET  /auth/me``        — Return the authenticated user's ID (requires
                               Bearer access token).

Design Patterns
---------------
- Router: ``APIRouter(prefix="/auth", tags=["auth"])`` keeps auth routes
  isolated from other route modules and easy to include/exclude.
- Dependency Injection: ``JWTService`` and ``OAuthService`` are injected via
  ``Depends`` to support overriding in tests.
- Graceful degradation: If no OAuth providers are configured, the login and
  callback endpoints return HTTP 503 with an explanatory message.

Public API
----------
- router: ``APIRouter`` instance to include in the FastAPI application.

Dependencies
------------
- graphclaw.auth.jwt: JWTService.
- graphclaw.auth.oauth: OAuthService.
- graphclaw.auth.middleware: require_auth, get_jwt_service.
- fastapi: APIRouter, Depends, HTTPException, Request (third-party).
- fastapi.responses: RedirectResponse (third-party).
- pydantic: BaseModel (third-party).
- jose: JWTError (third-party).
- logging, os: stdlib.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError
from pydantic import BaseModel

from graphclaw.auth.jwt import JWTService
from graphclaw.auth.middleware import get_current_user_id, get_jwt_service
from graphclaw.auth.oauth import OAuthService

# ── Pending-purge gate (FR-DEL-004) ───────────────────────────────────────────


class PendingPurgeDetail(BaseModel):
    """Body returned with HTTP 423 when the user has a pending purge."""

    code: str = "PENDING_PURGE"
    purge_after: str  # ISO-8601
    purge_initiated_at: str  # ISO-8601 (archived_at)


async def _check_pending_purge_gate(request: Request, user_id: str) -> None:
    """Raise HTTP 423 Locked if the user has a pending purge (FR-DEL-004).

    Called after user_id is resolved.  Reads from the graph store on
    app.state; no-ops gracefully when the store is unavailable.
    """
    graph_store = getattr(request.app.state, "graph_store", None)
    if graph_store is None:
        return
    try:
        node = await graph_store.get_node(user_id, include_archived=True)
    except Exception:  # noqa: BLE001
        return  # Non-fatal; let the login proceed if store is unreachable.
    if node is None:
        return
    purge_after = getattr(node, "purge_after", None)
    purge_cancelled_at = getattr(node, "purge_cancelled_at", None)
    if purge_after is not None and purge_cancelled_at is None:
        archived_at = getattr(node, "archived_at", None)
        detail = PendingPurgeDetail(
            purge_after=purge_after.isoformat()
            if hasattr(purge_after, "isoformat")
            else str(purge_after),
            purge_initiated_at=archived_at.isoformat()
            if archived_at and hasattr(archived_at, "isoformat")
            else "",
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=detail.model_dump(),
        )


from graphclaw.auth.provisioning import UserProvisioningService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_SUPPORTED_PROVIDERS = {"google", "github", "microsoft"}
_LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1"}

# ── Module-level OAuthService singleton ────────────────────────────────────────

_oauth_service: OAuthService | None = None


def get_oauth_service() -> OAuthService:
    """FastAPI dependency that returns the singleton ``OAuthService``.

    Initialises the service from environment variables on the first call.
    Subsequent calls return the cached instance.

    Returns
    -------
    OAuthService:
        The application-wide OAuth service instance.
    """
    global _oauth_service  # noqa: PLW0603
    if _oauth_service is None:
        logger.debug("OAuthService singleton: initialising from environment")
        _oauth_service = OAuthService.from_env()
    return _oauth_service


async def get_provisioning_service(
    request: Request,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> UserProvisioningService | None:
    """Return a ``UserProvisioningService`` backed by ``app.state`` services.

    Returns ``None`` when the graph store or storage client is not yet
    initialised (e.g. at startup or in tests without a DB).  Callers
    must handle ``None`` gracefully by falling back to token-only issuance.
    """
    graph_store = getattr(request.app.state, "graph_store", None)
    storage_client = getattr(request.app.state, "storage_client", None)
    if graph_store is None or storage_client is None:
        return None
    return UserProvisioningService(
        graph_store=graph_store,
        storage_client=storage_client,
        jwt_service=jwt_service,
    )


# ── Request / Response models ──────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    """Request body for ``POST /auth/refresh``."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for ``POST /auth/logout``."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Response body for token issuance endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900  # 15 minutes in seconds


class ExchangeResponse(TokenResponse):
    """Extended response for /auth/exchange — includes user profile."""

    user_id: str = ""
    role: str = "USER"
    display_name: str = ""
    email: str = ""


# ── Helper: build redirect URI ─────────────────────────────────────────────────


def _build_redirect_uri(provider_name: str) -> str:
    """Build the OAuth callback redirect URI for *provider_name*.

    Reads ``OAUTH_REDIRECT_BASE_URL`` from the environment (defaults to
    ``http://localhost:8000`` for local dev).

    Parameters
    ----------
    provider_name:
        The OAuth provider name (e.g. ``"google"``).

    Returns
    -------
    str:
        Full callback URL, e.g. ``https://api.graphclaw.ai/auth/callback?provider=google``.
    """
    base_url_raw = os.environ.get("OAUTH_REDIRECT_BASE_URL", "http://localhost:8000")
    base_url = _normalize_redirect_base_url(base_url_raw)

    allowlist = _load_redirect_allowlist()
    if base_url not in allowlist:
        raise ValueError("OAUTH_REDIRECT_BASE_URL must appear in OAUTH_REDIRECT_ALLOWLIST")

    return f"{base_url}/auth/callback?provider={provider_name}"


def _load_redirect_allowlist() -> set[str]:
    """Parse and normalize OAUTH_REDIRECT_ALLOWLIST into a strict set.

    The allowlist is a comma-separated list of absolute base URLs.
    When unset, it defaults to local development only.
    """
    raw = os.environ.get("OAUTH_REDIRECT_ALLOWLIST", "")
    if not raw.strip():
        return {"http://localhost:8000"}

    allowed: set[str] = set()
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        allowed.add(_normalize_redirect_base_url(candidate))
    if not allowed:
        raise ValueError("OAUTH_REDIRECT_ALLOWLIST cannot be empty when configured")
    return allowed


def _normalize_redirect_base_url(base_url: str) -> str:
    """Validate and normalize a redirect base URL for OAuth callback construction."""
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OAuth redirect base URL must use http or https")
    if not parsed.hostname:
        raise ValueError("OAuth redirect base URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("OAuth redirect base URL must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("OAuth redirect base URL must not include query or fragment")

    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in _LOCALHOST_NAMES:
        raise ValueError("Non-localhost OAuth redirect base URLs must use https")

    host_for_netloc = f"[{host}]" if ":" in host else host
    netloc = f"{host_for_netloc}:{parsed.port}" if parsed.port else host_for_netloc

    normalized_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, netloc, normalized_path, "", "", ""))


_OTC_PREFIX = "auth:otc:"
_OTC_TTL_SECONDS = 30


def _build_cockpit_url() -> str:
    """Return the cockpit base URL from COCKPIT_BASE_URL env var.

    Defaults to http://localhost:3000 for local development.
    """
    return os.environ.get("COCKPIT_BASE_URL", "http://localhost:3000").rstrip("/")


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "/login",
    status_code=302,
    summary="Initiate OAuth 2.0 login",
    description=(
        "Redirects the browser to the chosen identity provider's authorization page. "
        "The ``provider`` query parameter must be one of ``google``, ``github``, "
        "or ``microsoft``.  The IdP must be configured via the corresponding "
        "``OAUTH_<PROVIDER>_CLIENT_ID`` and ``OAUTH_<PROVIDER>_CLIENT_SECRET`` "
        "environment variables."
    ),
)
async def login(
    provider: str,
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    """Redirect to the IdP authorization URL with PKCE + CSRF state.

    Parameters
    ----------
    provider:
        IdP name — one of ``"google"``, ``"github"``, ``"microsoft"``.
    oauth_service:
        ``OAuthService`` injected via ``Depends``.

    Returns
    -------
    RedirectResponse:
        HTTP 302 redirect to the IdP authorization page.

    Raises
    ------
    HTTPException(400):
        If *provider* is not a supported value.
    HTTPException(503):
        If the requested provider is not configured (missing env vars).
    """
    provider = provider.lower().strip()
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Choose: google, github, microsoft.",
        )

    try:
        redirect_uri = _build_redirect_uri(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        authorization_url, state = await oauth_service.get_authorization_url(
            provider_name=provider,
            redirect_uri=redirect_uri,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    logger.info("auth/login: redirecting to %s (state=%s)", provider, state)
    return RedirectResponse(url=authorization_url, status_code=302)


@router.get(
    "/callback",
    response_model=TokenResponse,
    summary="OAuth 2.0 callback — exchange code for tokens",
    description=(
        "Receives the IdP authorization callback, validates the CSRF state, "
        "exchanges the authorization code for an access token, fetches the user "
        "profile, provisions a UserNode if needed, and returns GraphClaw access "
        "+ refresh tokens."
    ),
)
async def callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    jwt_service: JWTService = Depends(get_jwt_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
    provisioning_service: UserProvisioningService | None = Depends(get_provisioning_service),
) -> RedirectResponse:
    """Exchange OAuth authorization code for GraphClaw JWT tokens.

    Parameters
    ----------
    provider:
        IdP name — one of ``"google"``, ``"github"``, ``"microsoft"``.
    code:
        Authorization code received from the IdP.
    state:
        CSRF state token to validate against the stored value.
    jwt_service:
        ``JWTService`` injected via ``Depends``.
    oauth_service:
        ``OAuthService`` injected via ``Depends``.
    provisioning_service:
        ``UserProvisioningService`` injected via ``Depends``; ``None`` when the
        graph store is not initialised (dev / test without DB).

    Returns
    -------
    dict:
        ``{"access_token", "refresh_token", "token_type", "expires_in"}``.

    Raises
    ------
    HTTPException(400):
        If the state is invalid/expired or code exchange fails.
    HTTPException(503):
        If the provider is not configured.
    """
    provider = provider.lower().strip()
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Choose: google, github, microsoft.",
        )

    try:
        redirect_uri = _build_redirect_uri(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        userinfo = await oauth_service.exchange_code(
            provider_name=provider,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
        )
    except ValueError as exc:
        logger.warning("auth/callback: exchange_code failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    provider_name: str = userinfo.get("provider", provider)
    provider_user_id: str = userinfo.get("provider_user_id", "")
    email: str = userinfo.get("email", "")
    name: str = userinfo.get("name", "")
    oauth_subject = f"{provider_name}:{provider_user_id}"

    # ── User provisioning — create/lookup UserNode + WorkspaceNode ───────────
    if provisioning_service is not None:
        try:
            from graphclaw.cross_tenant.acl import CallerContext as _CallerContext  # noqa: PLC0415

            _provisioning_ctx = _CallerContext(
                user_id=oauth_subject,
                org_id="default",
                principal="agent_principal",
            )
            result = await provisioning_service.provision_new_user(
                oauth_subject=oauth_subject,
                email=email,
                display_name=name,
                provider=provider_name,
                caller_context=_provisioning_ctx,
            )
            logger.info(
                "auth/callback: provisioned user_id=%s is_new=%s provider=%s email=%s",
                result.user_id,
                result.is_new_user,
                provider_name,
                email,
            )
            # FR-DEL-004: block sign-in when user has pending purge.
            await _check_pending_purge_gate(request, result.user_id)
            return await _issue_otc_redirect(
                request=request,
                user_id=result.user_id,
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                role="USER",
                display_name=name,
                email=email,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "auth/callback: provisioning failed for %s: %s",
                email,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User provisioning is temporarily unavailable. Please try again.",
            ) from exc

    # ── Fallback: token-only (no DB configured — dev/test mode only) ─────────
    logger.info(
        "auth/callback: token-only mode (no provisioning service) for provider=%s email=%s",
        provider_name,
        email,
    )
    access_token = jwt_service.issue_access_token(oauth_subject)
    refresh_token = jwt_service.issue_refresh_token(oauth_subject)
    return await _issue_otc_redirect(
        request=request,
        user_id=oauth_subject,
        access_token=access_token,
        refresh_token=refresh_token,
        role="USER",
        display_name=name,
        email=email,
    )


async def _issue_otc_redirect(
    request: Request,
    user_id: str,
    access_token: str,
    refresh_token: str,
    role: str,
    display_name: str = "",
    email: str = "",
) -> RedirectResponse:
    """Store tokens in Redis under a one-time code and redirect browser to cockpit."""
    from urllib.parse import quote

    otc = secrets.token_urlsafe(32)
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        await redis.set(
            f"{_OTC_PREFIX}{otc}",
            f"{user_id}|{access_token}|{refresh_token}|{role}|{display_name}|{email}",
            ex=_OTC_TTL_SECONDS,
        )
    else:
        # Redis unavailable — fall back to query-param delivery (dev only)
        logger.warning(
            "auth/callback: Redis unavailable, falling back to query-param token delivery"
        )
        cockpit = _build_cockpit_url()
        return RedirectResponse(
            f"{cockpit}/auth/callback?access_token={access_token}&refresh_token={refresh_token}"
            f"&user_id={user_id}&role={role}&display_name={quote(display_name)}&email={quote(email)}",
            status_code=302,
        )
    cockpit = _build_cockpit_url()
    return RedirectResponse(f"{cockpit}/auth/callback?code={otc}", status_code=302)


class ExchangeRequest(BaseModel):
    """Request body for ``POST /auth/exchange``."""

    code: str


@router.post(
    "/exchange",
    response_model=ExchangeResponse,
    summary="Exchange one-time code for tokens",
    description=(
        "Exchanges a short-lived one-time code (issued by the OAuth callback redirect) "
        "for a real access + refresh token pair. The code is deleted after first use "
        "and expires in 30 seconds."
    ),
)
async def exchange(
    body: ExchangeRequest,
    request: Request,
) -> dict[str, Any]:
    """Exchange a one-time code for tokens."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth exchange requires Redis",
        )
    key = f"{_OTC_PREFIX}{body.code}"
    value: str | None = await redis.get(key)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired exchange code",
        )
    await redis.delete(key)
    parts = value.split("|", 5)
    user_id, access_token, refresh_token, role = parts[0], parts[1], parts[2], parts[3]
    display_name = parts[4] if len(parts) > 4 else ""
    email = parts[5] if len(parts) > 5 else ""
    logger.info("auth/exchange: redeemed OTC for user_id=%s", user_id)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
        "user_id": user_id,
        "role": role,
        "display_name": display_name,
        "email": email,
    }


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token",
    description=(
        "Validates the supplied refresh token, revokes it, and issues a fresh "
        "access + refresh token pair.  The old refresh token is immediately "
        "invalidated to prevent reuse (refresh token rotation)."
    ),
)
async def refresh(
    body: RefreshRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, Any]:
    """Rotate a refresh token and return a new token pair.

    Parameters
    ----------
    body:
        ``{"refresh_token": str}``.
    jwt_service:
        ``JWTService`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"access_token", "refresh_token", "token_type", "expires_in"}``.

    Raises
    ------
    HTTPException(401):
        If the refresh token is invalid, expired, or revoked.
    HTTPException(400):
        If the token is not of type ``"refresh"``.
    """
    try:
        payload = await jwt_service.verify_token_async(body.refresh_token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided token is not a refresh token",
        )

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing subject claim",
        )

    # Revoke the old refresh token (rotation)
    await jwt_service.revoke_token(body.refresh_token)

    new_access_token = jwt_service.issue_access_token(user_id)
    new_refresh_token = jwt_service.issue_refresh_token(user_id)

    logger.debug("auth/refresh: rotated refresh token for user_id=%s", user_id)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
    }


@router.post(
    "/logout",
    summary="Logout — revoke refresh token",
    description=(
        "Revokes the supplied refresh token, preventing it from being used to "
        "obtain new access tokens.  Access tokens are short-lived (15 min) and "
        "expire naturally; they cannot be revoked individually via this endpoint."
    ),
)
async def logout(
    body: LogoutRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, bool]:
    """Revoke a refresh token.

    Parameters
    ----------
    body:
        ``{"refresh_token": str}``.
    jwt_service:
        ``JWTService`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"ok": True}``.

    Notes
    -----
    This endpoint always returns ``{"ok": true}`` even if the token is already
    expired or invalid, to prevent information leakage.
    """
    try:
        await jwt_service.revoke_token(body.refresh_token)
    except Exception as exc:  # noqa: BLE001
        # Log but do not expose internal errors — always return ok
        logger.warning("auth/logout: revoke_token raised unexpectedly: %s", exc)

    logger.debug("auth/logout: refresh token revoked")
    return {"ok": True}


class DevTokenRequest(BaseModel):
    """Request body for ``POST /auth/dev-token`` (development only)."""

    user_id: str = "USER-dev-001"
    role: str = "ADMIN"


@router.post(
    "/dev-token",
    response_model=TokenResponse,
    summary="Issue a dev JWT (development mode only)",
    description=(
        "Issues a real RS256 JWT for the given ``user_id`` without OAuth. "
        "**Only available when ``ENVIRONMENT=development``.**  "
        "Returns HTTP 403 in production."
    ),
    include_in_schema=os.environ.get("ENVIRONMENT", "production") == "development",
)
async def dev_token(
    body: DevTokenRequest,
    request: Request,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, Any]:
    """Issue a dev access + refresh token pair without OAuth.

    Only works when ``ENVIRONMENT=development``.  Also provisions a minimal
    UserNode in the graph store if one does not already exist, so that all
    Wave 5+ admin/trigger endpoints work out of the box in dev mode.
    """
    if os.environ.get("ENVIRONMENT", "production") != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token endpoint is disabled in production",
        )
    access_token = jwt_service.issue_access_token(body.user_id, role=body.role)
    refresh_token = jwt_service.issue_refresh_token(body.user_id)

    # Provision minimal UserNode in graph (idempotent – only creates if absent)
    graph_store = getattr(request.app.state, "graph_store", None)
    if graph_store is not None:
        try:
            from graphclaw.cross_tenant.acl import CallerContext  # noqa: PLC0415
            from graphclaw.models.nodes import UserNode  # noqa: PLC0415

            ctx = CallerContext(
                user_id=body.user_id,
                org_id="default",
                principal="agent_principal",
            )
            existing = await graph_store.get_node(body.user_id, caller_context=ctx)
            if existing is None:
                from graphclaw.models.base import utcnow  # noqa: PLC0415

                now = utcnow()
                node = UserNode(
                    id=body.user_id,
                    name=f"Dev User ({body.user_id})",
                    email=f"{body.user_id.lower()}@dev.local",
                    role=body.role,
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                )
                await graph_store.create_node(node, caller_context=ctx)
                logger.info("auth/dev-token: provisioned UserNode for %s", body.user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auth/dev-token: could not provision UserNode for %s: %s", body.user_id, exc
            )

    logger.info("auth/dev-token: issued token for user_id=%s role=%s", body.user_id, body.role)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 900,
        "user_id": body.user_id,
        "role": body.role,
    }


@router.get(
    "/me",
    summary="Get current authenticated user",
    description=(
        "Returns the authenticated user's platform ID and profile. "
        "Requires a valid Bearer access token in the ``Authorization`` header."
    ),
)
async def me(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Return the authenticated user's platform ID and profile from the graph.

    Returns
    -------
    dict:
        ``{"user_id", "token_type", "display_name", "email"}`` — name/email
        are empty strings when the graph store is unavailable or the UserNode
        is not yet provisioned.
    """
    graph_store = getattr(request.app.state, "graph_store", None)
    display_name = ""
    email = ""
    if graph_store is not None:
        try:
            from graphclaw.cross_tenant.acl import CallerContext as _CallerContext  # noqa: PLC0415

            _me_ctx = _CallerContext(user_id=user_id, org_id="default", principal="agent_principal")
            nodes = await graph_store.list_nodes(
                "UserNode", {"id": user_id}, caller_context=_me_ctx
            )
            if nodes:
                node = nodes[0]
                display_name = node.get("name", "")
                email = node.get("email", "")
        except Exception:  # noqa: BLE001
            logger.warning("auth/me: failed to fetch UserNode for user_id=%s", user_id)
    return {
        "user_id": user_id,
        "token_type": "access",
        "display_name": display_name,
        "email": email,
    }
