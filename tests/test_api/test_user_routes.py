# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_user_routes — FR-UI-002 user/orgs endpoint tests.

Verifies GET /app/v1/user/orgs:
  AC1: Returns orgs where user is the owner.
  AC2: Returns orgs where user is an active member.
  AC3: Excludes orgs where user has no membership.
  AC4: Excludes INACTIVE members.
  AC5: Returns empty list when no orgs exist.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeGraphStore

_TEST_USER = "USER-test-org-001"


def _make_app(store: FakeGraphStore) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)
    app.dependency_overrides[require_auth] = lambda: _TEST_USER
    app.dependency_overrides[get_graph_store] = lambda: store
    return app


_ORG_OWNED = {
    "id": "ORG-owned-001",
    "name": "Owned Corp",
    "owner_id": _TEST_USER,
    "members": [],
    "domain": "owned.com",
}

_ORG_MEMBER = {
    "id": "ORG-member-002",
    "name": "Member Inc",
    "owner_id": "USER-other-999",
    "members": [
        {"user_id": _TEST_USER, "role": "MEMBER", "status": "ACTIVE"},
    ],
    "domain": None,
}

_ORG_INACTIVE = {
    "id": "ORG-inactive-003",
    "name": "Inactive Org",
    "owner_id": "USER-other-999",
    "members": [
        {"user_id": _TEST_USER, "role": "MEMBER", "status": "INACTIVE"},
    ],
    "domain": None,
}

_ORG_OTHER = {
    "id": "ORG-other-004",
    "name": "Other Org",
    "owner_id": "USER-other-999",
    "members": [
        {"user_id": "USER-different-888", "role": "ADMIN", "status": "ACTIVE"},
    ],
    "domain": None,
}


class TestListUserOrgs:
    def test_returns_owned_org(self) -> None:
        store = FakeGraphStore()
        store._nodes["ORG-owned-001"] = _ORG_OWNED
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["org_id"] == "ORG-owned-001"
        assert data[0]["role"] == "OWNER"

    def test_returns_member_org(self) -> None:
        store = FakeGraphStore()
        store._nodes["ORG-member-002"] = _ORG_MEMBER
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["org_id"] == "ORG-member-002"
        assert data[0]["role"] == "MEMBER"

    def test_excludes_inactive_member(self) -> None:
        store = FakeGraphStore()
        store._nodes["ORG-inactive-003"] = _ORG_INACTIVE
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        assert r.json() == []

    def test_excludes_other_user_orgs(self) -> None:
        store = FakeGraphStore()
        store._nodes["ORG-other-004"] = _ORG_OTHER
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        assert r.json() == []

    def test_empty_list_when_no_orgs(self) -> None:
        store = FakeGraphStore()
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_both_owned_and_member(self) -> None:
        store = FakeGraphStore()
        store._nodes["ORG-owned-001"] = _ORG_OWNED
        store._nodes["ORG-member-002"] = _ORG_MEMBER
        store._nodes["ORG-inactive-003"] = _ORG_INACTIVE
        store._nodes["ORG-other-004"] = _ORG_OTHER
        app = _make_app(store)
        with TestClient(app) as client:
            r = client.get("/app/v1/user/orgs")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        ids = {d["org_id"] for d in data}
        assert ids == {"ORG-owned-001", "ORG-member-002"}
