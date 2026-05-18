# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_notification_routes — API tests for the notification inbox.

GC-B-NOT-W09-001 — Notification Route Tests

Layer: L3 API
Build wave: W9
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.notifications import get_notification_service
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeNotificationService

_TEST_USER = "test-user-notif-001"
_OTHER_USER = "test-user-notif-002"

_fake_svc = FakeNotificationService()


def _make_app(user_id: str = _TEST_USER) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return user_id

    async def _fake_svc_dep() -> FakeNotificationService:
        return _fake_svc

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_notification_service] = _fake_svc_dep
    return app


def _seed(user_id: str = _TEST_USER, is_read: bool = False) -> str:
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        notif_id = loop.run_until_complete(
            _fake_svc.create(user_id, "task.needs_attention", "Test title", "Test body")
        )
    finally:
        loop.close()
    if is_read:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_fake_svc.mark_read(notif_id, user_id))
        finally:
            loop.close()
    return notif_id


@pytest.fixture(autouse=True)
def clear_svc():
    _fake_svc.clear()
    yield
    _fake_svc.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture()
def no_auth_client() -> TestClient:
    app = FastAPI()
    app.include_router(app_router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /app/v1/notifications
# ---------------------------------------------------------------------------


def test_list_empty(client: TestClient) -> None:
    r = client.get("/app/v1/notifications")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["unread_count"] == 0
    assert body["next_cursor"] is None


def test_list_with_items(client: TestClient) -> None:
    _seed()
    _seed()
    r = client.get("/app/v1/notifications")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["unread_count"] == 2
    item = body["items"][0]
    assert "id" in item
    assert "title" in item
    assert item["is_read"] is False


def test_list_only_own_notifications(client: TestClient) -> None:
    _seed(user_id=_TEST_USER)
    _seed(user_id=_OTHER_USER)
    r = client.get("/app/v1/notifications")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert all(i["title"] == "Test title" for i in items)


def test_list_excludes_dismissed(client: TestClient) -> None:
    notif_id = _seed()
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_fake_svc.dismiss(notif_id, _TEST_USER))
    finally:
        loop.close()
    r = client.get("/app/v1/notifications")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_unread_count_decreases_after_read(client: TestClient) -> None:
    notif_id = _seed()
    r1 = client.get("/app/v1/notifications")
    assert r1.json()["unread_count"] == 1
    client.patch(f"/app/v1/notifications/{notif_id}/read")
    r2 = client.get("/app/v1/notifications")
    assert r2.json()["unread_count"] == 0


def test_list_requires_auth(no_auth_client: TestClient) -> None:
    r = no_auth_client.get("/app/v1/notifications")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PATCH /app/v1/notifications/{id}/read
# ---------------------------------------------------------------------------


def test_mark_read_success(client: TestClient) -> None:
    notif_id = _seed()
    r = client.patch(f"/app/v1/notifications/{notif_id}/read")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == notif_id


def test_mark_read_not_found(client: TestClient) -> None:
    r = client.patch("/app/v1/notifications/00000000-0000-0000-0000-000000000000/read")
    assert r.status_code == 404


def test_mark_read_other_user_returns_404(client: TestClient) -> None:
    other_id = _seed(user_id=_OTHER_USER)
    r = client.patch(f"/app/v1/notifications/{other_id}/read")
    assert r.status_code == 404


def test_mark_read_requires_auth(no_auth_client: TestClient) -> None:
    r = no_auth_client.patch("/app/v1/notifications/any-id/read")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /app/v1/notifications/read-all
# ---------------------------------------------------------------------------


def test_mark_all_read(client: TestClient) -> None:
    _seed()
    _seed()
    r = client.post("/app/v1/notifications/read-all")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["updated"] == 2
    r2 = client.get("/app/v1/notifications")
    assert r2.json()["unread_count"] == 0


def test_mark_all_read_empty(client: TestClient) -> None:
    r = client.post("/app/v1/notifications/read-all")
    assert r.status_code == 200
    assert r.json()["updated"] == 0


def test_mark_all_read_skips_already_read(client: TestClient) -> None:
    _seed(is_read=True)
    _seed()
    r = client.post("/app/v1/notifications/read-all")
    assert r.json()["updated"] == 1


# ---------------------------------------------------------------------------
# DELETE /app/v1/notifications/{id}
# ---------------------------------------------------------------------------


def test_dismiss_success(client: TestClient) -> None:
    notif_id = _seed()
    r = client.delete(f"/app/v1/notifications/{notif_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r2 = client.get("/app/v1/notifications")
    assert r2.json()["items"] == []


def test_dismiss_not_found(client: TestClient) -> None:
    r = client.delete("/app/v1/notifications/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_dismiss_is_soft_delete(client: TestClient) -> None:
    notif_id = _seed()
    client.delete(f"/app/v1/notifications/{notif_id}")
    row = next((r for r in _fake_svc._rows if r["id"] == notif_id), None)
    assert row is not None
    assert row["dismissed_at"] is not None


def test_dismiss_other_user_returns_404(client: TestClient) -> None:
    other_id = _seed(user_id=_OTHER_USER)
    r = client.delete(f"/app/v1/notifications/{other_id}")
    assert r.status_code == 404
