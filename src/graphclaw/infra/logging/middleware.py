"""graphclaw.infra.logging.middleware — HTTP request/response logging middleware.

Sets session_id in ContextVar at request entry so all downstream loggers in
the same async context inherit it automatically via SessionFilter.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from graphclaw.infra.logging.context import generate_session_id, set_session_id

logger = logging.getLogger("graphclaw.http")

_EXCLUDED_PATHS: frozenset[str] = frozenset({"/health", "/health/ready", "/ready", "/metrics"})


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every HTTP request with method, path, status, latency, user_id.

    Sets session_id for the request context so all downstream log calls in the
    same async context carry the same session_id automatically.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        request_id = str(uuid.uuid4())
        session_id = request.headers.get("X-Session-ID") or generate_session_id()
        set_session_id(session_id)

        t0 = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - t0) * 1000)

        user_id = str(getattr(getattr(request, "state", None), "user_id", "") or "")

        logger.info(
            "http.request",
            extra={
                "event_type": "http.request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "user_id": user_id,
                "request_id": request_id,
                "session_id": session_id,
            },
        )
        return response
