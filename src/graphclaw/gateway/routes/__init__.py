"""graphclaw.gateway.routes — FastAPI router sub-package for the channel gateway.

Description
-----------
Collects and re-exports the individual FastAPI ``APIRouter`` instances that
make up the gateway's HTTP surface.  Importing from this package gives callers
a single namespace for all route modules.

Design Patterns
---------------
- Sub-package Router: Each channel concern (health, inbound, outbound) lives in
  its own module with its own ``APIRouter``.  The application factory (``create_app``
  in ``graphclaw.gateway.app``) can include them individually with distinct
  prefixes and tags.

Public API
----------
- health: ``APIRouter`` for liveness and readiness probes.
- inbound: ``APIRouter`` for accepting inbound messages.
- outbound: ``APIRouter`` for queuing outbound messages.

Dependencies
------------
- graphclaw.gateway.routes.health: health router.
- graphclaw.gateway.routes.inbound: inbound router.
- graphclaw.gateway.routes.outbound: outbound router.

Notes
-----
Routers in this package use FastAPI dependency injection (``Depends``) to
obtain the broker and logger from ``graphclaw.gateway.deps``.
"""
from __future__ import annotations

from graphclaw.gateway.routes import health, inbound, outbound

__all__ = ["health", "inbound", "outbound"]
