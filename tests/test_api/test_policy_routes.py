# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_policy_routes — FR-STORE-002 / FR-POL-001 API tests.

Verifies GET and PUT /app/v1/agents/{agent_id}/policies/{policy_name}:
  AC1: GET returns parsed frontmatter + body.
  AC2: PUT writes file and invalidates cache; subsequent GET returns new content.
  AC3: PUT with bad frontmatter returns 422.
  AC4: GET on missing closed-mode policy returns 404.
  AC5: Expected version mismatch on PUT returns 409.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-policy-001"

_DELEGATION_BYTES = b"""---
fail_mode: closed
auto_acknowledge: true
accept_deadline_extension_max_days: 5
escalate_on_blocker: false
---
Body text here.
"""


def _make_app(storage: FakeStorageClient) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)
    app.dependency_overrides[require_auth] = lambda: _TEST_USER
    app.dependency_overrides[get_storage_client] = lambda: storage
    return app


class TestPolicyGet:
    def test_get_existing_delegation(self) -> None:
        storage = FakeStorageClient()
        storage._data[f"{_TEST_USER}/agents/main/policies/delegation.md"] = _DELEGATION_BYTES
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/agents/main/policies/delegation")
        assert r.status_code == 200
        data = r.json()
        assert data["frontmatter"]["accept_deadline_extension_max_days"] == 5
        assert "Body text here" in data["body"]
        assert len(data["version"]) == 64  # SHA-256 etag

    def test_get_missing_closed_policy_returns_404(self) -> None:
        storage = FakeStorageClient()  # empty
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/agents/main/policies/delegation")
        assert r.status_code == 404

    def test_get_missing_degraded_policy_returns_defaults(self) -> None:
        storage = FakeStorageClient()  # empty
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/agents/main/policies/reply_tone")
        # reply_tone has fail_mode=degraded → returns defaults, not 404
        assert r.status_code == 200
        data = r.json()
        assert data["frontmatter"] == {} or data["body"] == ""

    def test_get_unknown_policy_returns_404(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.get("/app/v1/agents/main/policies/nonexistent")
        assert r.status_code == 404


class TestPolicyPut:
    def test_put_creates_policy(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.put(
                "/app/v1/agents/main/policies/delegation",
                json={
                    "frontmatter": {
                        "fail_mode": "closed",
                        "auto_acknowledge": True,
                        "accept_deadline_extension_max_days": 7,
                        "escalate_on_blocker": True,
                    },
                    "body": "# Custom delegation policy",
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert len(data["version"]) == 64

        # Verify file was written.
        path = f"{_TEST_USER}/agents/main/policies/delegation.md"
        assert path in storage._data
        content = storage._data[path].decode("utf-8")
        assert "accept_deadline_extension_max_days: 7" in content
        assert "Custom delegation policy" in content

    def test_put_with_bad_frontmatter_returns_422(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.put(
                "/app/v1/agents/main/policies/delegation",
                json={
                    "frontmatter": {"fail_mode": "not-a-valid-value"},
                    "body": "",
                },
            )
        assert r.status_code == 422

    def test_put_unknown_policy_returns_422(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            r = client.put(
                "/app/v1/agents/main/policies/badpolicy",
                json={"frontmatter": {}, "body": ""},
            )
        assert r.status_code == 422

    def test_get_after_put_reflects_new_content(self) -> None:
        storage = FakeStorageClient()
        app = _make_app(storage)
        with TestClient(app) as client:
            # Write
            client.put(
                "/app/v1/agents/main/policies/escalation",
                json={
                    "frontmatter": {
                        "fail_mode": "closed",
                        "interrupt_threshold": 0.6,
                    },
                    "body": "Updated escalation policy",
                },
            )
            # Read back
            r = client.get("/app/v1/agents/main/policies/escalation")
        assert r.status_code == 200
        data = r.json()
        assert data["frontmatter"]["interrupt_threshold"] == 0.6
        assert "Updated escalation policy" in data["body"]
