"""graphclaw.gateway.deps — FastAPI dependency injection for gateway services.

Description
-----------
Provides module-level service singletons and FastAPI ``Depends``-compatible
async getter functions for the ``MessageBroker`` used by gateway route handlers.
The singletons are initialised via ``init_services`` (called during lifespan
startup) and cleaned up via ``shutdown_services`` (called during lifespan
shutdown).

Logging is configured here via ``configure_logging()`` from
``graphclaw.infra.logging``. The logging system uses stdlib
``QueueHandler + QueueListener`` running in a dedicated OS thread — immune to
asyncio event loop congestion. No per-route logger dependency is needed;
all modules use ``logging.getLogger(__name__)`` directly.

Design Patterns
---------------
- Service Locator / DI Provider: Module-level ``_broker`` variable acts as a
  minimal service locator.  FastAPI's ``Depends`` mechanism wraps the getter so
  routes receive the dependency without referencing globals.
- Lifecycle Management: ``init_services`` and ``shutdown_services`` map
  directly onto the FastAPI lifespan context manager pattern.

Public API
----------
- get_broker: FastAPI dependency that returns the active ``MessageBroker``.
- init_services: Initialise broker and logging from environment variables.
- shutdown_services: Gracefully stop and release broker and logging.

Dependencies
------------
- graphclaw.infra.broker: MessageBroker, RedisMessageBroker.
- graphclaw.infra.logging: configure_logging, stop_logging.
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
from graphclaw.infra.logging import configure_logging, stop_logging

# ---------------------------------------------------------------------------
# Module-level singletons (set by init_services / cleared by shutdown_services)
# ---------------------------------------------------------------------------

_broker: MessageBroker | None = None


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


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


async def init_services(
    redis_url: str | None = None,
    service_name: str = "gateway",
) -> None:
    """Initialise the broker singleton and configure unified stdlib logging.

    Parameters
    ----------
    redis_url:
        Redis connection URL.  Falls back to the ``REDIS_URL`` environment
        variable, then to ``"redis://localhost:6379"``.
    service_name:
        Logical service name embedded in every structured log entry.
    """
    global _broker

    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    _broker = RedisMessageBroker(url=url)

    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    sink_names = [
        name.strip().lower()
        for name in os.environ.get("LOG_SINKS", "stdout").split(",")
        if name.strip()
    ]
    if not sink_names:
        sink_names = ["stdout"]

    configure_logging(
        service_name=service_name,
        log_level=log_level,
        sink_names=sink_names,
        storage_bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
        storage_endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL") or None,
        storage_region=os.environ.get("STORAGE_REGION", "us-east-1"),
        cloudwatch_region=os.environ.get("CLOUDWATCH_REGION", "us-east-1"),
        cloudwatch_log_group_prefix=os.environ.get(
            "CLOUDWATCH_LOG_GROUP_PREFIX", "/graphclaw"
        ),
        llm_trace_enabled=(
            os.environ.get("LLM_TRACE", "").lower() == "true"
            or log_level == "DEBUG"
        ),
        llm_trace_path=os.environ.get("LLM_TRACE_PATH") or None,
    )


async def shutdown_services() -> None:
    """Gracefully stop and release the broker singleton and logging system."""
    global _broker

    stop_logging()

    if _broker is not None:
        await _broker.close()
        _broker = None
