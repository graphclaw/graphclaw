# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.notifications — Notification inbox endpoints.

Endpoints
---------
  GET    /app/v1/notifications              — Paginated list + unread count.
  PATCH  /app/v1/notifications/{id}/read    — Mark one notification read.
  POST   /app/v1/notifications/read-all     — Mark all notifications read.
  DELETE /app/v1/notifications/{id}         — Soft-delete (dismiss) one notification.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from graphclaw.api.deps import CurrentUserDep
from graphclaw.notifications.models import (
    DismissResponse,
    MarkReadResponse,
    NotificationListResponse,
    ReadAllResponse,
)
from graphclaw.notifications.service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def get_notification_service(request: Request) -> NotificationService:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool is not initialised",
        )
    return NotificationService(pool)


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


@router.get(
    "",
    response_model=NotificationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List notifications",
)
async def list_notifications(
    user_id: CurrentUserDep,
    svc: NotificationServiceDep,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> NotificationListResponse:
    items, unread_count, next_cursor = await svc.list_for_user(user_id, limit, cursor)
    return NotificationListResponse(
        items=items,
        unread_count=unread_count,
        next_cursor=next_cursor,
    )


@router.patch(
    "/{notification_id}/read",
    response_model=MarkReadResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark one notification read",
)
async def mark_notification_read(
    notification_id: str,
    user_id: CurrentUserDep,
    svc: NotificationServiceDep,
) -> MarkReadResponse:
    updated = await svc.mark_read(notification_id, user_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return MarkReadResponse(id=notification_id)


@router.post(
    "/read-all",
    response_model=ReadAllResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark all notifications read",
)
async def mark_all_read(
    user_id: CurrentUserDep,
    svc: NotificationServiceDep,
) -> ReadAllResponse:
    updated = await svc.mark_all_read(user_id)
    return ReadAllResponse(updated=updated)


@router.delete(
    "/{notification_id}",
    response_model=DismissResponse,
    status_code=status.HTTP_200_OK,
    summary="Dismiss (soft-delete) a notification",
)
async def dismiss_notification(
    notification_id: str,
    user_id: CurrentUserDep,
    svc: NotificationServiceDep,
) -> DismissResponse:
    dismissed = await svc.dismiss(notification_id, user_id)
    if not dismissed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return DismissResponse(id=notification_id)
