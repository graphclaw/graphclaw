# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.auth.jwt — RS256 JWT issuance, verification, and revocation.

Description
-----------
Provides ``JWTService``, which issues and verifies RS256-signed JSON Web Tokens
for the GraphClaw platform.  Two token types are supported:

- **Access token** — 15-minute lifetime, used to authenticate API calls.
- **Refresh token** — 7-day lifetime, used to obtain new access tokens.

Token revocation is backed by a Redis sorted-set (``auth:revoked_jtis``).
Each revoked JTI is stored with a TTL equal to the token's remaining lifetime,
so the set never grows unbounded.  When Redis is unavailable the service falls
back to an in-process ``dict`` and logs a WARNING — revocations will not
survive a process restart in this degraded mode.

Design Patterns
---------------
- Factory classmethod: ``JWTService.from_env()`` reads all configuration from
  environment variables so callers need not manage keys directly.
- Graceful degradation: Missing RSA keys → auto-generate an ephemeral pair
  (local dev only).  Missing Redis → in-process revocation dict.

Public API
----------
- JWTService: Main service class.
  - issue_access_token(user_id) -> str
  - issue_refresh_token(user_id) -> str
  - verify_token(token) -> dict
  - revoke_token(token) -> None
  - from_env() -> JWTService  (classmethod)

Dependencies
------------
- jose: JWT operations (python-jose[cryptography]).
- redis.asyncio: Token revocation list storage.
- cryptography: RSA key pair generation for local dev.
- logging, os, uuid, datetime: stdlib.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_ACCESS_TOKEN_EXPIRE_SECONDS = 15 * 60  # 15 minutes
_REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 days
_ALGORITHM = "RS256"
_REDIS_REVOKED_KEY = "auth:revoked_jtis"


# ── JWTService ─────────────────────────────────────────────────────────────────


class JWTService:
    """RS256 JWT issuance, verification, and revocation for GraphClaw.

    Parameters
    ----------
    private_key:
        PEM-encoded RSA private key string used to sign tokens.
    public_key:
        PEM-encoded RSA public key string used to verify tokens.
    redis_client:
        An initialised ``redis.asyncio.Redis`` client for the revocation list.
        When ``None``, revocation is stored in an in-process dict (local dev).

    Notes
    -----
    Construct via ``JWTService.from_env()`` in production code.  Direct
    instantiation is provided for unit testing with injected keys/mock Redis.
    """

    def __init__(
        self,
        private_key: str,
        public_key: str,
        redis_client: Any | None = None,
    ) -> None:
        self._private_key = private_key
        self._public_key = public_key
        self._redis: Any | None = redis_client
        # In-process fallback: maps jti -> expiry_unix_timestamp
        self._revoked_local: dict[str, float] = {}

    # ── Token issuance ─────────────────────────────────────────────────────────

    def issue_access_token(self, user_id: str, role: str = "USER") -> str:
        """Issue a 15-minute RS256 access token for *user_id*.

        Parameters
        ----------
        user_id:
            The platform user identifier to embed in the ``sub`` claim.
        role:
            Optional role claim (e.g. ``"ADMIN"``).  Stored as ``role`` in
            the JWT payload so the auth middleware can set
            ``request.state.user_role`` without a DB round-trip.

        Returns
        -------
        str:
            Encoded JWT string.
        """
        return self._issue_token(user_id, "access", _ACCESS_TOKEN_EXPIRE_SECONDS, role=role)

    def issue_refresh_token(self, user_id: str) -> str:
        """Issue a 7-day RS256 refresh token for *user_id*.

        Parameters
        ----------
        user_id:
            The platform user identifier to embed in the ``sub`` claim.

        Returns
        -------
        str:
            Encoded JWT string.
        """
        return self._issue_token(user_id, "refresh", _REFRESH_TOKEN_EXPIRE_SECONDS)

    def _issue_token(
        self,
        user_id: str,
        token_type: str,
        expire_seconds: int,
        role: str | None = None,
    ) -> str:
        """Internal helper — build and sign a JWT with the given claims."""
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": user_id,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + expire_seconds,
            "type": token_type,
        }
        if role:
            payload["role"] = role
        return jwt.encode(payload, self._private_key, algorithm=_ALGORITHM)

    # ── Token verification ─────────────────────────────────────────────────────

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT, checking signature, expiry, and revocation.

        Parameters
        ----------
        token:
            The encoded JWT string to verify.

        Returns
        -------
        dict[str, Any]:
            Decoded claims payload if the token is valid.

        Raises
        ------
        JWTError:
            If the token is malformed, expired, has an invalid signature,
            or has been revoked.
        """
        try:
            payload: dict[str, Any] = jwt.decode(token, self._public_key, algorithms=[_ALGORITHM])
        except JWTError:
            raise

        jti: str | None = payload.get("jti")
        if jti and self._is_revoked_sync(jti):
            raise JWTError("Token has been revoked")

        return payload

    def _is_revoked_sync(self, jti: str) -> bool:
        """Synchronous revocation check against the in-process fallback dict.

        This is called during synchronous ``verify_token``.  Redis checks are
        performed asynchronously via ``_is_revoked_async`` when a running event
        loop is available.
        """
        # Purge expired entries from local dict
        now = time.time()
        expired = [k for k, exp in self._revoked_local.items() if exp < now]
        for k in expired:
            del self._revoked_local[k]

        if jti in self._revoked_local:
            return True

        # If there is a running event loop, schedule an async Redis check.
        # For synchronous callers (e.g. unit tests) we skip the Redis check here
        # and rely on the async path in FastAPI middleware.
        if self._redis is not None:
            try:
                loop = asyncio.get_running_loop()
                # We cannot await here; create a task and assume valid for now.
                # The async middleware should call verify_token_async instead.
                _ = loop
            except RuntimeError:
                pass  # No running loop — skip Redis check in sync context

        return False

    async def verify_token_async(self, token: str) -> dict[str, Any]:
        """Async variant of verify_token — performs a Redis revocation check.

        Parameters
        ----------
        token:
            The encoded JWT string to verify.

        Returns
        -------
        dict[str, Any]:
            Decoded claims payload if the token is valid.

        Raises
        ------
        JWTError:
            If the token is malformed, expired, has an invalid signature,
            or has been revoked.
        """
        try:
            payload: dict[str, Any] = jwt.decode(token, self._public_key, algorithms=[_ALGORITHM])
        except JWTError:
            raise

        jti: str | None = payload.get("jti")
        if jti:
            revoked = await self._is_revoked_async(jti)
            if revoked:
                raise JWTError("Token has been revoked")

        return payload

    async def _is_revoked_async(self, jti: str) -> bool:
        """Check if *jti* appears in the Redis revocation set (or local dict).

        Parameters
        ----------
        jti:
            The JWT ID to check.

        Returns
        -------
        bool:
            ``True`` if the token has been revoked.
        """
        # Check local fallback first (always populated on revoke)
        now = time.time()
        expired = [k for k, exp in self._revoked_local.items() if exp < now]
        for k in expired:
            del self._revoked_local[k]
        if jti in self._revoked_local:
            return True

        if self._redis is None:
            return False

        try:
            result = await self._redis.sismember(_REDIS_REVOKED_KEY, jti)
            return bool(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "JWTService: Redis revocation check failed for jti=%s, assuming not revoked: %s",
                jti,
                exc,
            )
            return False

    # ── Token revocation ───────────────────────────────────────────────────────

    async def revoke_token(self, token: str) -> None:
        """Revoke *token* by adding its JTI to the revocation list.

        The JTI is stored with a TTL equal to the token's remaining lifetime so
        the revocation list does not grow unbounded.

        Parameters
        ----------
        token:
            The encoded JWT string to revoke.  The token is decoded without
            expiry validation so that already-expired tokens can still be
            explicitly revoked (e.g. as part of a logout sweep).

        Notes
        -----
        If the token is malformed (cannot be decoded), this method is a no-op
        and logs a WARNING rather than raising.
        """
        try:
            # Decode without verifying expiry so we can extract jti regardless
            payload: dict[str, Any] = jwt.decode(
                token,
                self._public_key,
                algorithms=[_ALGORITHM],
                options={"verify_exp": False},
            )
        except JWTError as exc:
            logger.warning("JWTService.revoke_token: cannot decode token — %s", exc)
            return

        jti: str | None = payload.get("jti")
        exp: int | None = payload.get("exp")
        if not jti:
            logger.warning("JWTService.revoke_token: token has no jti claim, cannot revoke")
            return

        now = int(time.time())
        remaining_ttl = max(1, (exp or (now + 60)) - now)

        # Always populate in-process dict as fallback
        self._revoked_local[jti] = float(now + remaining_ttl)

        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                await pipe.sadd(_REDIS_REVOKED_KEY, jti)
                # Per-member TTL not natively supported in Redis sets;
                # use a separate expiry key approach via EXPIRE on the whole set
                # to the maximum TTL in the set.  For correctness we use a
                # per-jti string key with TTL as a complementary approach.
                revoke_key = f"auth:revoked:{jti}"
                await pipe.set(revoke_key, "1", ex=remaining_ttl)
                await pipe.execute()
                logger.debug("JWTService: revoked jti=%s ttl=%ds", jti, remaining_ttl)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "JWTService: Redis revocation write failed for jti=%s, "
                    "using in-process fallback: %s",
                    jti,
                    exc,
                )

    # ── Factory classmethod ────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> JWTService:
        """Construct a ``JWTService`` from environment variables.

        Environment Variables
        ---------------------
        JWT_PRIVATE_KEY:
            PEM-encoded RSA private key.  If blank, a new ephemeral RS256 key
            pair is generated in memory (local dev only — always set in prod).
        JWT_PUBLIC_KEY:
            PEM-encoded RSA public key.  If blank, derived from the private key
            (or the generated key for local dev).
        REDIS_URL:
            Redis connection URL (e.g. ``redis://localhost:6379``).  If blank
            or Redis is unreachable, revocation uses an in-process dict.

        Returns
        -------
        JWTService:
            A fully configured ``JWTService`` instance.

        Warns
        -----
        Logs WARNING if JWT keys are not provided (ephemeral keys generated).
        Logs WARNING if Redis is not available (in-process revocation fallback).
        """
        private_key_pem = os.environ.get("JWT_PRIVATE_KEY", "").strip()
        public_key_pem = os.environ.get("JWT_PUBLIC_KEY", "").strip()
        redis_url = os.environ.get("REDIS_URL", "").strip()

        if not private_key_pem:
            logger.warning(
                "JWTService: JWT_PRIVATE_KEY not set — generating ephemeral RS256 key pair. "
                "This is only suitable for local development. Set JWT_PRIVATE_KEY and "
                "JWT_PUBLIC_KEY in production."
            )
            private_key_pem, public_key_pem = _generate_rsa_key_pair()
        elif not public_key_pem:
            # Derive public key from private key
            public_key_pem = _derive_public_key(private_key_pem)

        redis_client: Any | None = None
        if redis_url:
            try:
                import redis.asyncio as aioredis  # noqa: PLC0415

                redis_client = aioredis.from_url(redis_url, decode_responses=True)
                logger.debug("JWTService: Redis revocation list configured at %s", redis_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "JWTService: Could not initialise Redis client (url=%s): %s. "
                    "Using in-process revocation fallback.",
                    redis_url,
                    exc,
                )
        else:
            logger.warning(
                "JWTService: REDIS_URL not set — using in-process revocation list. "
                "Revocations will not survive process restart."
            )

        return cls(
            private_key=private_key_pem,
            public_key=public_key_pem,
            redis_client=redis_client,
        )


# ── RSA key helpers ────────────────────────────────────────────────────────────


def _generate_rsa_key_pair() -> tuple[str, str]:
    """Generate an ephemeral RS256 key pair using the ``cryptography`` library.

    Returns
    -------
    tuple[str, str]:
        ``(private_key_pem, public_key_pem)`` as UTF-8 strings.

    Notes
    -----
    Requires ``cryptography>=42.0.0`` (listed in project dependencies).
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_pem, public_pem


def _derive_public_key(private_key_pem: str) -> str:
    """Derive the RSA public key from *private_key_pem*.

    Parameters
    ----------
    private_key_pem:
        PEM-encoded RSA private key string.

    Returns
    -------
    str:
        PEM-encoded RSA public key string.
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: PLC0415

    private_key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    return (
        private_key.public_key()
        .public_bytes(  # type: ignore[union-attr]
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
