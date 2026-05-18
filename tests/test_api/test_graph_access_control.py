# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_graph_access_control — User-scope authorization for task endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store, get_query_engine
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeGraphStore

_CURRENT_USER = "USER-requester-001"


def _make_client(graph_store: FakeGraphStore) -> TestClient:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _CURRENT_USER

    async def _fake_graph_store() -> FakeGraphStore:
        return graph_store

    async def _fake_query_engine() -> object:
        return object()

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_graph_store] = _fake_graph_store
    app.dependency_overrides[get_query_engine] = _fake_query_engine
    return TestClient(app)


def test_get_task_returns_403_for_non_owner() -> None:
    store = FakeGraphStore()
    store._nodes["TSK-403-GET"] = {
        "id": "TSK-403-GET",
        "title": "Restricted task",
        "owned_by": "USER-other-001",
    }
    client = _make_client(store)

    response = client.get("/app/v1/graph/tasks/TSK-403-GET")

    assert response.status_code == 403


def test_get_task_allows_access_via_owned_by_edge() -> None:
    store = FakeGraphStore()
    store._nodes["TSK-EDGE-ALLOW"] = {
        "id": "TSK-EDGE-ALLOW",
        "title": "Edge-authorized task",
    }
    store._edges.append(
        {
            "id": "edge-owned-by",
            "source_id": "TSK-EDGE-ALLOW",
            "target_id": _CURRENT_USER,
            "edge_type": "OWNED_BY",
        }
    )
    client = _make_client(store)

    response = client.get("/app/v1/graph/tasks/TSK-EDGE-ALLOW")

    assert response.status_code == 200
    assert response.json()["task"]["id"] == "TSK-EDGE-ALLOW"


def test_patch_task_returns_403_for_non_owner() -> None:
    store = FakeGraphStore()
    store._nodes["TSK-403-PATCH"] = {
        "id": "TSK-403-PATCH",
        "title": "Restricted update",
        "owned_by": "USER-other-002",
    }
    client = _make_client(store)

    response = client.patch("/app/v1/graph/tasks/TSK-403-PATCH", json={"title": "Updated"})

    assert response.status_code == 403


def test_delete_task_returns_403_for_non_owner() -> None:
    store = FakeGraphStore()
    store._nodes["TSK-403-DELETE"] = {
        "id": "TSK-403-DELETE",
        "title": "Restricted delete",
        "owned_by": "USER-other-003",
    }
    client = _make_client(store)

    response = client.delete("/app/v1/graph/tasks/TSK-403-DELETE")

    assert response.status_code == 403
