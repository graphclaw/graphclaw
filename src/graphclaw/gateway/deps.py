"""graphclaw.gateway.deps — FastAPI dependency injection for gateway services.

Description
-----------
Provides module-level service singletons and FastAPI ``Depends``-compatible
async getter functions for the ``MessageBroker`` and ``AsyncLogger`` used by
gateway route handlers.  The singletons are initialised via ``init_services``
(called during lifespan startup) and cleaned up via ``shutdown_services``
(called during lifespan shutdown).

Design Patterns
---------------
- Service Locator / DI Provider: Module-level ``_broker`` and ``_logger``
  variables act as a minimal service locator.  FastAPI's ``Depends`` mechanism
  wraps the getters so routes receive dependencies without referencing globals.
- Lifecycle Management: ``init_services`` and ``shutdown_services`` map
  directly onto the FastAPI lifespan context manager pattern.

Public API
----------
- get_broker: FastAPI dependency that returns the active ``MessageBroker``.
- get_logger: FastAPI dependency that returns the active ``AsyncLogger``.
- init_services: Initialise broker and logger from environment variables.
- shutdown_services: Gracefully stop and release broker and logger.

Dependencies
------------
- graphclaw.infra.broker: MessageBroker, RedisMessageBroker.
- graphclaw.infra.logger: AsyncLogger.
- os: Environment variable access (stdlib).

Notes
-----
Routes that need the broker should declare ``broker: MessageBroker = Depends(get_broker)``
in their signature.  The dependency raises ``RuntimeError`` if called before
``init_services``, which surfaces as an HTTP 500 during development and
integration tests run without a real broker.

For unit tests, override the dependency via ``app.dependency_overrides``:

    app.dependency_overrides[get_broker] = lambda: mock_broker
"""

from __future__ import annotations

import os

from graphclaw.infra.broker import MessageBroker, RedisMessageBroker
from graphclaw.infra.logger import AsyncLogger

# ---------------------------------------------------------------------------
# Module-level singletons (set by init_services / cleared by shutdown_services)
# ---------------------------------------------------------------------------

_broker: MessageBroker | None = None
_logger: AsyncLogger | None = None


# ---------------------------------------------------------------------------
# FastAPI dependency callables
# ---------------------------------------------------------------------------


async def get_broker() -> MessageBroker:
    """Return the active ``MessageBroker`` singleton.

    Raises
    ------
    RuntimeError
        If ``init_services`` has not been called yet (i.e. the broker is
        ``None``).
    """
    if _broker is None:
        raise RuntimeError("MessageBroker not initialised — call init_services() first.")
    return _broker


async def get_logger() -> AsyncLogger:
    """Return the active ``AsyncLogger`` singleton.

    Raises
    ------
    RuntimeError
        If ``init_services`` has not been called yet (i.e. the logger is
        ``None``).
    """
    if _logger is None:
        raise RuntimeError("AsyncLogger not initialised — call init_services() first.")
    return _logger


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


async def init_services(
    redis_url: str | None = None,
    service_name: str = "gateway",
) -> None:
    """Initialise the broker and logger singletons.

    Parameters
    ----------
    redis_url:
        Redis connection URL.  Falls back to the ``REDIS_URL`` environment
        variable, then to ``"redis://localhost:6379"``.
    service_name:
        Logical service name embedded in every structured log entry.
    """
    global _broker, _logger

    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    _broker = RedisMessageBroker(url=url)

    _logger = AsyncLogger(service_name=service_name)
    await _logger.start()


async def shutdown_services() -> None:
    """Gracefully stop and release the broker and logger singletons."""
    global _broker, _logger

    if _logger is not None:
        await _logger.stop()
        _logger = None

    if _broker is not None:
        await _broker.close()
        _broker = None
