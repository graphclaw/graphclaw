"""graphclaw.gateway.routes.health — Liveness and readiness probe endpoints.

Description
-----------
Provides two health-check endpoints for the GraphClaw channel gateway:

- ``GET /health`` — Liveness probe.  Always returns HTTP 200 with
  ``{"status": "ok"}`` while the process is running.  No downstream checks
  are performed; this is suitable for container restart policies.

- ``GET /ready`` — Readiness probe.  Returns HTTP 200 when the broker and
  any other critical services are contactable.  Returns HTTP 503 when the
  broker is not configured or unavailable.  This is suitable for load-balancer
  traffic routing.

Design Patterns
---------------
- Probe Separation: Liveness (``/health``) and readiness (``/ready``) are kept
  on separate routes so orchestrators can apply different failure policies.
- Dependency Injection: The broker is obtained via a ``_get_broker_optional``
  dependency that wraps ``get_broker`` and returns ``None`` on failure rather
  than raising.  This allows ``dependency_overrides`` to work correctly in
  tests while gracefully degrading when the broker is unavailable.

Public API
----------
- router: ``APIRouter`` instance.  Include in the application via
  ``app.include_router(router, tags=["health"])``.

Dependencies
------------
- graphclaw.gateway.deps: get_broker.
- graphclaw.gateway.models: HealthStatus.
- fastapi: APIRouter, Depends, Response (third-party).

Notes
-----
The readiness probe currently treats the presence of an initialised broker as
sufficient evidence of readiness.  A future enhancement could perform a
round-trip publish/consume against a dedicated health queue to verify both
connectivity and queue depth.

``_get_broker_optional`` is used as the dependency for ``/ready`` so that
tests can override it via ``app.dependency_overrides`` and the endpoint
degrades gracefully to 503 when no broker is available.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from graphclaw.gateway.deps import get_broker
from graphclaw.gateway.models import HealthStatus
from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_broker_optional() -> MessageBroker | None:
    """Dependency that returns the broker or ``None`` if not initialised.

    Wraps ``get_broker`` so that the ``/ready`` endpoint can use
    FastAPI's ``dependency_overrides`` mechanism in tests while also
    degrading gracefully in production when the broker is unavailable.
    """
    try:
        return await get_broker()
    except (RuntimeError, Exception):  # noqa: BLE001
        return None


@router.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check() -> HealthStatus:
    """Liveness probe — always returns HTTP 200 while the process is alive."""
    return HealthStatus(
        status="ok",
        version="0.1.0",
        services={},
    )


@router.get("/ready", tags=["health"])
async def readiness_check(
    broker: MessageBroker | None = Depends(_get_broker_optional),
) -> JSONResponse:
    """Readiness probe — checks broker connectivity.

    Parameters
    ----------
    broker:
        Optional ``MessageBroker`` obtained via ``Depends``.  ``None``
        indicates the broker is not initialised or unavailable.

    Returns HTTP 200 with ``{"status": "ready"}`` when the broker is
    initialised and reachable.  Returns HTTP 503 with
    ``{"status": "degraded"}`` when the broker is unavailable.
    """
    if broker is None:
        logger.warning("Readiness check: broker not available")
        return JSONResponse(
            status_code=503,
            content=HealthStatus(
                status="degraded",
                version="0.1.0",
                services={"broker": "unavailable"},
            ).model_dump(),
        )

    return JSONResponse(
        status_code=200,
        content=HealthStatus(
            status="ready",
            version="0.1.0",
            services={"broker": "ok"},
        ).model_dump(),
    )
