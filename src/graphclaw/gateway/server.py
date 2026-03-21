"""graphclaw.gateway.server — ASGI entry point for the GraphClaw gateway.

Description
-----------
Provides the module-level ``app`` instance that uvicorn imports when started
with ``uvicorn graphclaw.gateway.server:app``.  The application is created
via ``create_app`` with a ``RedisMessageBroker`` wired to the ``REDIS_URL``
environment variable.

Design Patterns
---------------
- Module-level Factory Invocation: The ``app`` object is created at import
  time so that uvicorn's ``--reload`` flag detects changes and recreates the
  process.  The broker connection is established lazily during the FastAPI
  lifespan startup hook, not at import time.

Public API
----------
- app: The ``FastAPI`` ASGI application instance.

Dependencies
------------
- graphclaw.gateway.app: create_app factory.
- graphclaw.infra.broker: RedisMessageBroker.
- os: Environment variable access (stdlib).

Notes
-----
Run locally with::

    uvicorn graphclaw.gateway.server:app --host 0.0.0.0 --port 8000 --reload

Or via Docker Compose::

    docker compose up gateway
"""

from __future__ import annotations

import os

from graphclaw.gateway.app import create_app
from graphclaw.infra.broker import RedisMessageBroker

_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
_broker = RedisMessageBroker(url=_redis_url)

app = create_app(broker=_broker)
