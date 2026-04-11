"""tests.test_api.test_settings_extended_routes — Tests for extended /settings/* endpoints.

Description
-----------
Covers the eleven new settings endpoints added in Wave 4:

- GET/PATCH /app/v1/settings/profile
- POST      /app/v1/settings/channels/{ch}/activate
- DELETE    /app/v1/settings/channels/{ch}
- GET/PATCH /app/v1/settings/scoring-weights
- GET/POST  /app/v1/settings/organizations
- PATCH     /app/v1/settings/organizations/{id}
- POST/DELETE /app/v1/settings/llm-keys

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_auth``, ``get_storage_client``,
  ``get_graph_store``, and ``get_secrets_client`` are replaced with fakes.
- FakeSecretsClient: In-memory dict-backed SecretsClient supporting get/set/delete.
- FakeGraphStore / FakeStorageClient: Reused from conftest.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.deps: get_storage_client, get_graph_store, get_secrets_client.
- graphclaw.auth.middleware: require_auth.
- graphclaw.infra.secrets: SecretsClient.
- tests.test_api.conftest: FakeGraphStore, FakeStorageClient.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store, get_secrets_client, get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.infra.secrets import SecretsClient
from graphclaw.models.base import utcnow
from graphclaw.models.nodes import OrganizationNode, UserNode

from tests.test_api.conftest import FakeGraphStore, FakeStorageClient

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_TEST_USER = "USER-test-settings-ext-001"
_OTHER_USER = "USER-test-settings-ext-002"


class FakeSecretsClient(SecretsClient):
    """In-memory SecretsClient for testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def clear(self) -> None:
        self._store.clear()

    async def get_secret(self, key: str) -> str:
        if key not in self._store:
            raise KeyError(f"Secret '{key}' not found")
        return self._store[key]

    async def set_secret(self, key: str, value: str) -> None:
        self._store[key] = value

    async def delete_secret(self, key: str) -> None:
        if key not in self._store:
            raise KeyError(f"Secret '{key}' not found")
        del self._store[key]


# ---------------------------------------------------------------------------
# App factory helper
# ---------------------------------------------------------------------------


def _make_app(
    user_id: str = _TEST_USER,
    storage: FakeStorageClient | None = None,
    graph_store: FakeGraphStore | None = None,
    secrets: FakeSecretsClient | None = None,
) -> tuple[FastAPI, FakeStorageClient, FakeGraphStore, FakeSecretsClient]:
    if storage is None:
        storage = FakeStorageClient()
    if graph_store is None:
        graph_store = FakeGraphStore()
    if secrets is None:
        secrets = FakeSecretsClient()

    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return user_id

    async def _fake_storage() -> FakeStorageClient:
        return storage

    async def _fake_store() -> FakeGraphStore:
        return graph_store

    async def _fake_secrets() -> FakeSecretsClient:
        return secrets

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage
    app.dependency_overrides[get_graph_store] = _fake_store
    app.dependency_overrides[get_secrets_client] = _fake_secrets
    return app, storage, graph_store, secrets


def _seed_user(store: FakeGraphStore, user_id: str = _TEST_USER) -> None:
    """Seed a minimal UserNode dict directly into the fake graph store."""
    now = utcnow()
    # Seed as raw dict to bypass UserNode ID validation (test IDs may not match pattern)
    store._nodes[user_id] = {
        "id": user_id,
        "name": "Test User",
        "email": "test@example.com",
        "role": None,
        "timezone": "UTC",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "version": 0,
    }


def _seed_org(
    store: FakeGraphStore,
    org_id: str,
    owner_id: str,
    members: list[dict] | None = None,
) -> None:
    """Seed a minimal OrganizationNode dict into the fake graph store."""
    now = utcnow()
    store._nodes[org_id] = {
        "id": org_id,
        "name": "Test Org",
        "domain": "example.com",
        "owner_id": owner_id,
        "members": members or [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "version": 0,
    }


# ---------------------------------------------------------------------------
# GET /app/v1/settings/profile
# ---------------------------------------------------------------------------


def test_get_profile_no_node_returns_minimal() -> None:
    """GET /settings/profile returns minimal profile when UserNode absent."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/settings/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == _TEST_USER


def test_get_profile_with_node_returns_full_profile() -> None:
    """GET /settings/profile returns full profile when UserNode is in graph."""
    graph = FakeGraphStore()
    _seed_user(graph)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.get("/app/v1/settings/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test User"
    assert data["email"] == "test@example.com"
    assert data["timezone"] == "UTC"


# ---------------------------------------------------------------------------
# PATCH /app/v1/settings/profile
# ---------------------------------------------------------------------------


def test_patch_profile_updates_timezone() -> None:
    """PATCH /settings/profile updates timezone field on the UserNode."""
    graph = FakeGraphStore()
    _seed_user(graph)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.patch("/app/v1/settings/profile", json={"timezone": "America/New_York"})
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == "America/New_York"


def test_patch_profile_no_node_returns_404() -> None:
    """PATCH /settings/profile returns 404 when UserNode does not exist."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.patch("/app/v1/settings/profile", json={"timezone": "UTC"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /app/v1/settings/channels/{ch}/activate
# ---------------------------------------------------------------------------


def test_activate_channel_adds_to_settings() -> None:
    """POST /settings/channels/email/activate adds channel to settings."""
    app, storage, _, _ = _make_app()
    client = TestClient(app)
    response = client.post("/app/v1/settings/channels/email/activate", json={"config": {}})
    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == "email"
    assert data["enabled"] is True


def test_activate_channel_reactivates_disabled_channel() -> None:
    """POST activate re-enables a previously disabled channel."""
    app, storage, _, _ = _make_app()
    client = TestClient(app)
    # Activate, then deactivate, then reactivate
    client.post("/app/v1/settings/channels/slack/activate", json={"config": {}})
    client.delete("/app/v1/settings/channels/slack")
    response = client.post("/app/v1/settings/channels/slack/activate", json={"config": {}})
    assert response.status_code == 200
    assert response.json()["enabled"] is True


# ---------------------------------------------------------------------------
# DELETE /app/v1/settings/channels/{ch}
# ---------------------------------------------------------------------------


def test_deactivate_channel_returns_204() -> None:
    """DELETE /settings/channels/{ch} returns 204 after disabling."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    # First activate the channel
    client.post("/app/v1/settings/channels/telegram/activate", json={"config": {}})
    # Then deactivate
    response = client.delete("/app/v1/settings/channels/telegram")
    assert response.status_code == 204


def test_deactivate_nonexistent_channel_returns_404() -> None:
    """DELETE /settings/channels/{ch} returns 404 for unknown channel."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.delete("/app/v1/settings/channels/whatsapp")
    assert response.status_code == 404


def test_channel_disabled_after_deactivate() -> None:
    """After DELETE, the channel is present with enabled=False in list."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    client.post("/app/v1/settings/channels/email/activate", json={"config": {}})
    client.delete("/app/v1/settings/channels/email")
    channels = client.get("/app/v1/settings/channels").json()
    email_ch = next((c for c in channels if c["channel"] == "email"), None)
    assert email_ch is not None
    assert email_ch["enabled"] is False


# ---------------------------------------------------------------------------
# GET /app/v1/settings/scoring-weights
# ---------------------------------------------------------------------------


def test_get_scoring_weights_returns_defaults() -> None:
    """GET /settings/scoring-weights returns default W1-W7 values for new user."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/settings/scoring-weights")
    assert response.status_code == 200
    data = response.json()
    assert "W1_timeline" in data
    assert "W7_constraint" in data
    # Defaults should sum to approximately 1.0
    total = sum(data[k] for k in data)
    assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# PATCH /app/v1/settings/scoring-weights
# ---------------------------------------------------------------------------


def test_patch_scoring_weights_updates_factor() -> None:
    """PATCH /settings/scoring-weights updates supplied factors."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.patch(
        "/app/v1/settings/scoring-weights",
        json={"W1_timeline": 0.30},
    )
    assert response.status_code == 200
    assert response.json()["W1_timeline"] == 0.30


def test_patch_scoring_weights_preserves_other_factors() -> None:
    """PATCH /settings/scoring-weights does not change omitted factors."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    original = client.get("/app/v1/settings/scoring-weights").json()
    client.patch("/app/v1/settings/scoring-weights", json={"W2_dependencies": 0.30})
    updated = client.get("/app/v1/settings/scoring-weights").json()
    assert updated["W2_dependencies"] == 0.30
    assert updated["W1_timeline"] == original["W1_timeline"]  # unchanged


# ---------------------------------------------------------------------------
# GET /app/v1/settings/organizations
# ---------------------------------------------------------------------------


def test_list_organizations_empty_for_new_user() -> None:
    """GET /settings/organizations returns [] for user with no orgs."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/settings/organizations")
    assert response.status_code == 200
    assert response.json() == []


def test_list_organizations_shows_owned_orgs() -> None:
    """GET /settings/organizations includes orgs owned by the user."""
    graph = FakeGraphStore()
    _seed_org(graph, "ORG-testorg001", owner_id=_TEST_USER)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.get("/app/v1/settings/organizations")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["org_id"] == "ORG-testorg001"
    assert data[0]["owner_id"] == _TEST_USER


def test_list_organizations_shows_member_orgs() -> None:
    """GET /settings/organizations includes orgs where user is a member."""
    graph = FakeGraphStore()
    _seed_org(
        graph,
        "ORG-testorg002",
        owner_id=_OTHER_USER,
        members=[{"user_id": _TEST_USER, "role": "MEMBER"}],
    )
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.get("/app/v1/settings/organizations")
    data = response.json()
    assert len(data) == 1
    assert data[0]["org_id"] == "ORG-testorg002"


def test_list_organizations_excludes_unrelated_orgs() -> None:
    """GET /settings/organizations does not return orgs the user is not in."""
    graph = FakeGraphStore()
    _seed_org(graph, "ORG-testorg003", owner_id=_OTHER_USER)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.get("/app/v1/settings/organizations")
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /app/v1/settings/organizations
# ---------------------------------------------------------------------------


def test_create_organization_returns_201() -> None:
    """POST /settings/organizations creates a new org and returns 201."""
    app, _, graph, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/settings/organizations",
        json={"name": "Acme Corp", "domain": "acme.com"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Acme Corp"
    assert data["domain"] == "acme.com"
    assert data["owner_id"] == _TEST_USER
    assert data["org_id"].startswith("ORG-")


def test_create_organization_persisted_in_graph() -> None:
    """POST /settings/organizations stores the OrganizationNode in the graph."""
    app, _, graph, _ = _make_app()
    client = TestClient(app)
    resp = client.post("/app/v1/settings/organizations", json={"name": "Test Org"})
    org_id = resp.json()["org_id"]
    assert org_id in graph._nodes


# ---------------------------------------------------------------------------
# PATCH /app/v1/settings/organizations/{id}
# ---------------------------------------------------------------------------


def test_patch_organization_updates_name() -> None:
    """PATCH /settings/organizations/{id} updates the org name."""
    graph = FakeGraphStore()
    _seed_org(graph, "ORG-patchme001", owner_id=_TEST_USER)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.patch(
        "/app/v1/settings/organizations/ORG-patchme001",
        json={"name": "Renamed Org"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Org"


def test_patch_organization_not_found_returns_404() -> None:
    """PATCH /settings/organizations/{id} returns 404 for unknown org."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.patch(
        "/app/v1/settings/organizations/ORG-ghost",
        json={"name": "Ghost Org"},
    )
    assert response.status_code == 404


def test_patch_organization_non_owner_returns_403() -> None:
    """PATCH /settings/organizations/{id} returns 403 for non-owner."""
    graph = FakeGraphStore()
    _seed_org(graph, "ORG-otherorg001", owner_id=_OTHER_USER)
    app, _, _, _ = _make_app(graph_store=graph)
    client = TestClient(app)
    response = client.patch(
        "/app/v1/settings/organizations/ORG-otherorg001",
        json={"name": "Hijacked Name"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /app/v1/settings/llm-keys
# ---------------------------------------------------------------------------


def test_store_llm_key_returns_200() -> None:
    """POST /settings/llm-keys stores the key and returns provider name."""
    app, _, _, secrets = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/settings/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "anthropic"
    assert data["stored"] is True


def test_store_llm_key_does_not_return_value() -> None:
    """POST /settings/llm-keys response does not contain the key value."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/settings/llm-keys",
        json={"provider": "openai", "api_key": "sk-super-secret"},
    )
    assert "sk-super-secret" not in response.text


def test_store_llm_key_persisted_in_secrets() -> None:
    """POST /settings/llm-keys actually persists to SecretsClient."""
    app, _, _, secrets = _make_app()
    client = TestClient(app)
    client.post(
        "/app/v1/settings/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-ant-test"},
    )
    expected_key = f"graphclaw/{_TEST_USER}/llm/anthropic"
    assert expected_key in secrets._store
    assert secrets._store[expected_key] == "sk-ant-test"


# ---------------------------------------------------------------------------
# DELETE /app/v1/settings/llm-keys/{provider}
# ---------------------------------------------------------------------------


def test_delete_llm_key_returns_204() -> None:
    """DELETE /settings/llm-keys/{provider} returns 204 after deletion."""
    app, _, _, secrets = _make_app()
    client = TestClient(app)
    # First store a key
    client.post(
        "/app/v1/settings/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-ant-test"},
    )
    # Then delete it
    response = client.delete("/app/v1/settings/llm-keys/anthropic")
    assert response.status_code == 204


def test_delete_llm_key_not_found_returns_404() -> None:
    """DELETE /settings/llm-keys/{provider} returns 404 for unknown provider."""
    app, _, _, _ = _make_app()
    client = TestClient(app)
    response = client.delete("/app/v1/settings/llm-keys/nonexistent-provider")
    assert response.status_code == 404


def test_delete_llm_key_removes_from_secrets() -> None:
    """DELETE /settings/llm-keys/{provider} removes the key from the secrets store."""
    app, _, _, secrets = _make_app()
    client = TestClient(app)
    client.post(
        "/app/v1/settings/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-ant-test"},
    )
    client.delete("/app/v1/settings/llm-keys/anthropic")
    expected_key = f"graphclaw/{_TEST_USER}/llm/anthropic"
    assert expected_key not in secrets._store
