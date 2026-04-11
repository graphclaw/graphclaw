"""Shared fixtures for admin API tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store, get_secrets_client, get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

from tests.test_api.conftest import FakeGraphStore, FakeStorageClient

_ADMIN_USER = "USER-admin-test-001"


class FakeSecretsClient:
    """Minimal in-memory SecretsClient for admin tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get_secret(self, key: str) -> str:
        if key not in self._store:
            raise KeyError(key)
        return self._store[key]

    async def set_secret(self, key: str, value: str) -> None:
        self._store[key] = value

    async def delete_secret(self, key: str) -> None:
        if key not in self._store:
            raise KeyError(key)
        del self._store[key]


def make_admin_app(
    user_id: str = _ADMIN_USER,
    storage: FakeStorageClient | None = None,
    graph_store: FakeGraphStore | None = None,
    secrets: FakeSecretsClient | None = None,
    role: str = "ADMIN",
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

    # Inject role into request.state via middleware override
    from starlette.middleware.base import BaseHTTPMiddleware

    class RoleMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_role = role
            return await call_next(request)

    app.add_middleware(RoleMiddleware)
    return app, storage, graph_store, secrets
