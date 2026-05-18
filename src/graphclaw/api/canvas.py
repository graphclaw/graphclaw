# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.canvas — Canvas layout persistence endpoints.

Description
-----------
Two thin endpoints that persist the cockpit Agent Canvas layout (node
positions, viewport zoom/pan) for the authenticated user.  The layout is a
purely UI-side artefact — no graph node is created.

Routes
------
GET  /app/v1/canvas/layout   — load canvas layout
PUT  /app/v1/canvas/layout   — save canvas layout

Storage layout
--------------
- ``agents/{user_id}/definitions/canvas-layout.json``

Design Patterns
---------------
- Thin persistence: GET returns the stored JSON blob directly; PUT writes it
  back.  Validation is minimal (must be a JSON object).
- 404 → empty: If no layout has been saved yet GET returns ``{}`` so the
  frontend can trigger auto-layout on first visit.

Public API
----------
- router: ``APIRouter`` for /canvas routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- fastapi: APIRouter, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas", tags=["app-api"])

_LAYOUT_TEMPLATE = "agents/{user_id}/definitions/canvas-layout.json"


def _layout_path(user_id: str) -> str:
    return _LAYOUT_TEMPLATE.format(user_id=user_id)


class CanvasLayout(BaseModel):
    """Canvas layout document — node positions and viewport state."""

    nodes: list[dict[str, Any]] = []
    viewport: dict[str, Any] = {}


@router.get(
    "/layout",
    response_model=CanvasLayout,
    status_code=status.HTTP_200_OK,
    summary="Get canvas layout",
    description="Return the saved canvas node positions and viewport state.",
)
async def get_canvas_layout(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> CanvasLayout:
    """Load the canvas layout from object storage."""
    try:
        raw = await storage_client.read(_layout_path(user_id))
        data = json.loads(raw.decode())
        return CanvasLayout(**data)
    except FileNotFoundError:
        # First visit — return empty layout so frontend triggers auto-layout
        return CanvasLayout()
    except Exception as exc:
        logger.warning("canvas: layout read failed for user_id=%s: %s", user_id, exc)
        return CanvasLayout()


@router.put(
    "/layout",
    response_model=CanvasLayout,
    status_code=status.HTTP_200_OK,
    summary="Save canvas layout",
    description="Persist the canvas node positions and viewport state.",
)
async def put_canvas_layout(
    body: CanvasLayout,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> CanvasLayout:
    """Write the canvas layout to object storage."""
    raw = json.dumps(body.model_dump(), default=str).encode()
    await storage_client.write(
        _layout_path(user_id),
        raw,
        content_type="application/json",
    )
    logger.debug("canvas: layout saved for user_id=%s", user_id)
    return body
