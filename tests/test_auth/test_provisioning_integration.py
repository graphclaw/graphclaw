# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for O-AUTH-01: UserProvisioningService wiring in auth callback.

Tests verify that:
1. UserProvisioningService.provision_new_user() creates a UserNode in AGE.
2. The WorkspaceNode is created and linked via an OWNS edge.
3. A second call with the same email is idempotent (returns existing user).
4. The auth callback endpoint calls provisioning when app.state is wired.

All tests run against a real PostgreSQL + Apache AGE instance and MinIO.

Run with::

    pytest tests/test_auth/test_provisioning_integration.py -m integration

DSN is read from TEST_DATABASE_URL; storage from STORAGE_ENDPOINT_URL / STORAGE_BUCKET.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Set MinIO credentials before any boto3/botocore import
os.environ.setdefault("AWS_ACCESS_KEY_ID", "graphclaw")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "graphclaw_dev")

from graphclaw.auth.middleware import get_jwt_service
from graphclaw.auth.provisioning import UserProvisioningService
from graphclaw.auth.routes import get_oauth_service, get_provisioning_service
from graphclaw.auth.routes import router as auth_router
from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.infra.storage import S3StorageClient

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
STORAGE_ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
STORAGE_REGION = os.getenv("STORAGE_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pool():
    p = await create_pool(TEST_DSN)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    return AgeGraphStore(pool)


@pytest.fixture
def storage():
    return S3StorageClient(
        bucket=STORAGE_BUCKET,
        endpoint_url=STORAGE_ENDPOINT,
        region=STORAGE_REGION,
    )


@pytest.fixture
def jwt_service():
    return get_jwt_service()


@pytest.fixture
def provisioning_svc(repo, storage, jwt_service):
    return UserProvisioningService(
        graph_store=repo,
        storage_client=storage,
        jwt_service=jwt_service,
    )


def _unique_email() -> str:
    return f"inttest-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------------------
# Direct provisioning service tests
# ---------------------------------------------------------------------------


class TestProvisioningServiceIntegration:
    """Tests for UserProvisioningService against real AGE + MinIO backends."""

    @pytest.mark.asyncio
    async def test_new_user_creates_usernode_in_age(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """provision_new_user() must create a UserNode in the AGE graph."""
        email = _unique_email()

        result = await provisioning_svc.provision_new_user(
            oauth_subject=f"google:{uuid.uuid4().hex[:12]}",
            email=email,
            display_name="Integration Tester",
            provider="google",
        )

        try:
            assert result.is_new_user is True
            assert result.user_id.startswith("USER-")
            assert result.workspace_id.startswith("WS-")
            assert result.access_token
            assert result.refresh_token

            # Verify UserNode exists in AGE
            raw_user = await repo.get_node(result.user_id)
            assert raw_user is not None, f"UserNode {result.user_id} not found in AGE"
            assert raw_user.get("email") == email

        finally:
            # Clean up — deprovision user
            await provisioning_svc.deprovision_user(result.user_id)

    @pytest.mark.asyncio
    async def test_new_user_creates_workspace_linked_via_owns_edge(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """A WorkspaceNode linked via OWNS edge must be created."""
        email = _unique_email()

        result = await provisioning_svc.provision_new_user(
            oauth_subject=f"github:{uuid.uuid4().hex[:12]}",
            email=email,
            display_name="Workspace Test User",
            provider="github",
        )

        try:
            # Verify WorkspaceNode in AGE
            raw_ws = await repo.get_node(result.workspace_id)
            assert raw_ws is not None, f"WorkspaceNode {result.workspace_id} not found"

            # Verify OWNS edge from user → workspace
            edges = await repo.get_edges(result.user_id, direction="out", edge_type="OWNS")
            ws_ids = {e.get("_end_id") for e in edges}
            assert result.workspace_id in ws_ids, (
                f"No OWNS edge from {result.user_id} to {result.workspace_id}"
            )

        finally:
            await provisioning_svc.deprovision_user(result.user_id)

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_existing_user(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """Calling provision_new_user twice with the same email is idempotent."""
        email = _unique_email()
        oauth_subject = f"google:{uuid.uuid4().hex[:12]}"

        result1 = await provisioning_svc.provision_new_user(
            oauth_subject=oauth_subject,
            email=email,
            display_name="First Call",
            provider="google",
        )

        try:
            result2 = await provisioning_svc.provision_new_user(
                oauth_subject=oauth_subject,
                email=email,
                display_name="Second Call",
                provider="google",
            )

            assert result2.is_new_user is False
            assert result2.user_id == result1.user_id
            assert result2.access_token  # still issues tokens
            assert result2.refresh_token

        finally:
            await provisioning_svc.deprovision_user(result1.user_id)

    @pytest.mark.asyncio
    async def test_deprovision_removes_usernode(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """deprovision_user() must delete the UserNode from AGE."""
        email = _unique_email()
        result = await provisioning_svc.provision_new_user(
            oauth_subject=f"microsoft:{uuid.uuid4().hex[:12]}",
            email=email,
            display_name="To Be Deleted",
            provider="microsoft",
        )
        user_id = result.user_id

        # Verify it exists first
        assert await repo.get_node(user_id) is not None

        await provisioning_svc.deprovision_user(user_id)

        # Verify it's gone
        assert await repo.get_node(user_id) is None, (
            f"UserNode {user_id} still present after deprovision"
        )


# ---------------------------------------------------------------------------
# HTTP callback endpoint test
# ---------------------------------------------------------------------------


class TestCallbackProvisionsUser:
    """End-to-end test: POST /auth/callback triggers provisioning via real DB."""

    @pytest.mark.asyncio
    async def test_callback_creates_usernode_via_provisioning(
        self, repo: AgeGraphStore, storage: S3StorageClient, jwt_service
    ):
        """The callback endpoint must call UserProvisioningService when app.state is wired."""
        from unittest.mock import AsyncMock, MagicMock

        provisioning_svc = UserProvisioningService(
            graph_store=repo,
            storage_client=storage,
            jwt_service=jwt_service,
        )

        # Build a minimal FastAPI app with the auth router and real provisioning
        test_app = FastAPI()
        test_app.include_router(auth_router)  # prefix=/auth

        # Mock the OAuth service to return canned userinfo
        unique_id = uuid.uuid4().hex[:12]
        email = f"cbtest-{unique_id}@example.com"

        mock_oauth = MagicMock()
        mock_oauth.exchange_code = AsyncMock(
            return_value={
                "provider": "google",
                "provider_user_id": unique_id,
                "email": email,
                "name": "Callback Test User",
            }
        )

        test_app.dependency_overrides[get_oauth_service] = lambda: mock_oauth
        test_app.dependency_overrides[get_provisioning_service] = lambda: provisioning_svc

        user_id_created = None
        try:
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/auth/callback",
                    params={"provider": "google", "code": "mock-code", "state": "mock-state"},
                )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "access_token" in body
            assert "refresh_token" in body

            # The callback's JWT sub should resolve to a real USER-{uuid} from provisioning
            # Verify a UserNode with that email was created in AGE
            nodes = await repo.list_nodes("UserNode", filters={"email": email})
            assert len(nodes) == 1, f"Expected 1 UserNode for {email}, got {len(nodes)}"
            user_id_created = nodes[0]["id"]

        finally:
            if user_id_created:
                await provisioning_svc.deprovision_user(user_id_created)


# ---------------------------------------------------------------------------
# oauth_subject-based idempotency integration tests
# ---------------------------------------------------------------------------


class TestOAuthSubjectIdempotency:
    """Integration tests verifying that oauth_subject drives stable user identity."""

    @pytest.mark.asyncio
    async def test_re_login_same_oauth_subject_returns_same_user_id(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """Two provision calls with the same oauth_subject must return the same user_id."""
        email = _unique_email()
        oauth_subject = f"google:{uuid.uuid4().hex[:12]}"

        result1 = await provisioning_svc.provision_new_user(
            oauth_subject=oauth_subject,
            email=email,
            display_name="Repeat Login",
            provider="google",
        )

        try:
            result2 = await provisioning_svc.provision_new_user(
                oauth_subject=oauth_subject,
                email=email,
                display_name="Repeat Login",
                provider="google",
            )

            assert result2.is_new_user is False
            assert result2.user_id == result1.user_id

        finally:
            await provisioning_svc.deprovision_user(result1.user_id)

    @pytest.mark.asyncio
    async def test_oauth_subject_stored_on_usernode_in_graph(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """The oauth_subject value must be persisted on the UserNode in AGE."""
        email = _unique_email()
        oauth_subject = f"google:{uuid.uuid4().hex[:12]}"

        result = await provisioning_svc.provision_new_user(
            oauth_subject=oauth_subject,
            email=email,
            display_name="Subject Persistence",
            provider="google",
        )

        try:
            raw_node = await repo.get_node(result.user_id)
            assert raw_node is not None
            assert raw_node.get("oauth_subject") == oauth_subject

        finally:
            await provisioning_svc.deprovision_user(result.user_id)

    @pytest.mark.asyncio
    async def test_re_login_by_oauth_subject_when_email_differs(
        self, provisioning_svc: UserProvisioningService, repo: AgeGraphStore
    ):
        """Re-login with same oauth_subject but a different email returns the existing user.

        This simulates the case where a user changes their email address on the
        provider side between logins — the provider identity (oauth_subject) is
        the authoritative lookup key.
        """
        original_email = _unique_email()
        oauth_subject = f"google:{uuid.uuid4().hex[:12]}"

        result1 = await provisioning_svc.provision_new_user(
            oauth_subject=oauth_subject,
            email=original_email,
            display_name="Email Changer",
            provider="google",
        )

        try:
            changed_email = _unique_email()
            result2 = await provisioning_svc.provision_new_user(
                oauth_subject=oauth_subject,
                email=changed_email,
                display_name="Email Changer",
                provider="google",
            )

            assert result2.is_new_user is False
            assert result2.user_id == result1.user_id

        finally:
            await provisioning_svc.deprovision_user(result1.user_id)
