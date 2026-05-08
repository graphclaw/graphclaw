# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.rate_limiter — Token-bucket rate limiting middleware.

Description
-----------
Rate Limiting Middleware using Redis for distributed state.
Limits per IP (unauthenticated) and per user_id (authenticated).

Limits (per PRD Phase 5):
- Unauthenticated: 30 req/min per IP
- Authenticated users: 300 req/min per user_id
- A2A agents: 60 req/min per api_key_id
- Webhook endpoints (/webhooks/*): 120 req/min per source IP

Design Patterns
---------------
- Strategy: ``RateLimiter`` encapsulates the sliding window algorithm;
  ``RateLimitMiddleware`` determines which key/limit applies to a request.
- Sliding Window: Uses Redis sorted sets to track request timestamps within
  the current window, providing smooth rate limiting without burst artifacts.

Public API
----------
- RateLimiter: Sliding window rate limiter backed by Redis sorted sets.
- RateLimitMiddleware: FastAPI/Starlette middleware applying rate limits.
- RATE_LIMITS: Dict mapping limit categories to (limit, window_seconds) tuples.

Dependencies
------------
- redis.asyncio: Async Redis client (redis-py).
- fastapi: Request, Response (third-party).
- starlette.middleware.base: BaseHTTPMiddleware (third-party).
- time, typing: stdlib.

Notes
-----
JWT decoding in the middleware is best-effort for rate limit key extraction
only. Full token verification is handled downstream by the auth middleware.
If decoding fails for any reason, the request falls back to IP-based limits.

License: Apache 2.0
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import redis.asyncio as aioredis
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_log = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded (not used directly by middleware)."""


class RateLimiter:
    """Sliding window rate limiter backed by Redis sorted sets."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis_url = redis_url
        self._client: aioredis.Redis | None = None

    async def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """Sliding window check using Redis sorted set.

        Parameters
        ----------
        key:
            Unique rate limit bucket identifier (e.g. ``"ip:1.2.3.4"``).
        limit:
            Maximum number of requests allowed in the window.
        window_seconds:
            Duration of the sliding window in seconds.

        Returns
        -------
        tuple[bool, int]:
            ``(allowed, remaining)`` — whether the request is allowed and how
            many requests remain in the current window.
        """
        try:
            client = await self._get_client()
            now = time.time()
            window_start = now - window_seconds
            redis_key = f"ratelimit:{key}"

            pipe = client.pipeline()
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            results = await pipe.execute()

            count = results[2]
            allowed = count <= limit
            remaining = max(0, limit - count)
            return allowed, remaining
        except Exception:  # noqa: BLE001
            # Fail-open: if Redis is unavailable, allow the request rather than
            # blocking all traffic.  A warning is logged for observability.
            _log.warning("rate_limiter: Redis unavailable, failing open for key=%s", key)
            return True, limit

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None


# ── Rate limits config ────────────────────────────────────────────────────────

RATE_LIMITS: dict[str, tuple[int, int]] = {
    "unauthenticated": (30, 60),  # 30 req / 60s per IP
    "authenticated": (300, 60),  # 300 req / 60s per user
    "a2a": (60, 60),  # 60 req / 60s per agent key
    "webhook": (120, 60),  # 120 req / 60s per IP
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware applying per-category rate limits.

    Applies limits in priority order:
    1. Webhook paths (``/webhooks/*``) — keyed by source IP.
    2. A2A task-update path (``/api/v1/task-update``) — keyed by API key header.
    3. Platform paths with a Bearer token — keyed by JWT ``sub`` claim.
    4. All other paths — keyed by source IP (unauthenticated limit).

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    redis_url:
        Redis connection URL for the distributed rate limit state store.
    """

    def __init__(self, app, redis_url: str = "redis://localhost:6379/0"):
        super().__init__(app)
        self.limiter = RateLimiter(redis_url)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        # Prefer real client IP forwarded by a trusted reverse proxy (nginx).
        # Falls back to direct TCP peer address when not behind a proxy.
        client_ip = (
            request.headers.get("X-Real-IP")
            or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or None)
            or (request.client.host if request.client else None)
            or "unknown"
        )

        # Auth and health paths are exempt: OAuth provider rate-limits auth
        # flows independently, and health checks must never be blocked.
        if path.startswith("/auth/") or path == "/health":
            return await call_next(request)

        # Determine key and limit
        if path.startswith("/webhooks/"):
            key = f"webhook:{client_ip}"
            limit, window = RATE_LIMITS["webhook"]
        elif path.startswith("/api/v1/task-update"):
            # A2A endpoint — key by API key header
            api_key = request.headers.get("X-Agent-Api-Key", client_ip)
            key = f"a2a:{api_key[:16]}"
            limit, window = RATE_LIMITS["a2a"]
        else:
            # Platform endpoints — key by user_id from JWT if available, else IP
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                # Extract sub from JWT without full verification (just for rate limit key).
                # Full verification happens in auth middleware — this is a best-effort key.
                try:
                    import base64
                    import json as _json

                    payload_part = auth_header.split(".")[1]
                    padded = payload_part + "=" * (-len(payload_part) % 4)
                    sub = _json.loads(base64.b64decode(padded)).get("sub", client_ip)
                    key = f"user:{sub}"
                    limit, window = RATE_LIMITS["authenticated"]
                except Exception:  # noqa: BLE001
                    key = f"ip:{client_ip}"
                    limit, window = RATE_LIMITS["unauthenticated"]
            else:
                key = f"ip:{client_ip}"
                limit, window = RATE_LIMITS["unauthenticated"]

        allowed, remaining = await self.limiter.is_allowed(key, limit, window)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after": window},
                headers={"Retry-After": str(window), "X-RateLimit-Remaining": "0"},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
