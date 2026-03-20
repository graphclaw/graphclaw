"""tests.test_auth.test_provisioning — Unit tests for UserProvisioningService.

Description
-----------
Tests for ``UserProvisioningService`` provisioning, idempotency, rollback, and
deprovisioning logic.  All infrastructure (``GraphStore``, ``StorageClient``,
``JWTService``) is mocked via ``AsyncMock`` / ``MagicMock``.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, calls the service, and asserts
  the expected side-effects.
- Side-effect mocking: ``create_node`` side_effect is used to simulate failure
  on the second call (WorkspaceNode creation) for rollback tests.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.auth.provisioning: UserProvisioningService, ProvisioningResult.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from graphclaw.auth.provisioning import ProvisioningResult, UserProvisioningService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_graph():
    store = AsyncMock()
    store.list_nodes = AsyncMock(return_value=[])   # no existing user by default
    store.create_node = AsyncMock(return_value={})
    store.delete_node = AsyncMock()
    store.create_edge = AsyncMock(return_value={})
    store.get_edges = AsyncMock(return_value=[])
    store.update_node = AsyncMock(return_value={})
    return store


@pytest.fixture
def mock_storage():
    s = AsyncMock()
    s.write = AsyncMock()
    s.delete = AsyncMock()
    return s


@pytest.fixture
def mock_jwt():
    j = MagicMock()
    j.issue_access_token = MagicMock(return_value="access-tok")
    j.issue_refresh_token = MagicMock(return_value="refresh-tok")
    return j


@pytest.fixture
def svc(mock_graph, mock_storage, mock_jwt):
    return UserProvisioningService(mock_graph, mock_storage, mock_jwt)


# ---------------------------------------------------------------------------
# provision_new_user — new user path
# ---------------------------------------------------------------------------


class TestProvisionNewUser:
    @pytest.mark.asyncio
    async def test_new_user_creates_user_node(self, svc, mock_graph):
        await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        assert mock_graph.create_node.call_count >= 2  # UserNode + WorkspaceNode

    @pytest.mark.asyncio
    async def test_new_user_writes_s3_prefix(self, svc, mock_storage):
        await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        assert mock_storage.write.called
        args = mock_storage.write.call_args[0]
        assert ".keep" in args[0]

    @pytest.mark.asyncio
    async def test_new_user_creates_workspace_node(self, svc, mock_graph):
        await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        # create_node called at least twice: UserNode and WorkspaceNode
        assert mock_graph.create_node.call_count >= 2

    @pytest.mark.asyncio
    async def test_new_user_creates_owns_edge(self, svc, mock_graph):
        await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        mock_graph.create_edge.assert_called()
        edge_type_arg = mock_graph.create_edge.call_args[1].get(
            "edge_type"
        ) or mock_graph.create_edge.call_args[0][2]
        assert "OWNS" in str(edge_type_arg)

    @pytest.mark.asyncio
    async def test_new_user_issues_tokens(self, svc, mock_jwt):
        result = await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        assert result.access_token == "access-tok"
        assert result.refresh_token == "refresh-tok"

    @pytest.mark.asyncio
    async def test_new_user_returns_is_new_user_true(self, svc):
        result = await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        assert isinstance(result, ProvisioningResult)
        assert result.is_new_user is True

    @pytest.mark.asyncio
    async def test_result_has_user_id_and_workspace_id(self, svc):
        result = await svc.provision_new_user(
            oauth_subject="google:123",
            email="alice@example.com",
            display_name="Alice",
            provider="google",
        )
        assert result.user_id.startswith("USER-")
        assert result.workspace_id.startswith("WS-")


# ---------------------------------------------------------------------------
# provision_new_user — existing user (idempotency)
# ---------------------------------------------------------------------------


class TestProvisionExistingUser:
    @pytest.mark.asyncio
    async def test_existing_user_returns_is_new_user_false(self, svc, mock_graph):
        existing_record = {
            "id": "USER-existing-abc",
            "email": "bob@example.com",
            "name": "Bob",
        }
        mock_graph.list_nodes = AsyncMock(return_value=[existing_record])
        # get_edges for default workspace lookup
        mock_graph.get_edges = AsyncMock(return_value=[])

        result = await svc.provision_new_user(
            oauth_subject="github:456",
            email="bob@example.com",
            display_name="Bob",
            provider="github",
        )
        assert result.is_new_user is False

    @pytest.mark.asyncio
    async def test_existing_user_does_not_call_create_node(self, svc, mock_graph):
        existing_record = {
            "id": "USER-existing-abc",
            "email": "bob@example.com",
            "name": "Bob",
        }
        mock_graph.list_nodes = AsyncMock(return_value=[existing_record])
        mock_graph.get_edges = AsyncMock(return_value=[])

        await svc.provision_new_user(
            oauth_subject="github:456",
            email="bob@example.com",
            display_name="Bob",
            provider="github",
        )
        mock_graph.create_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_user_returns_existing_user_id(self, svc, mock_graph):
        existing_record = {
            "id": "USER-existing-abc",
            "email": "bob@example.com",
            "name": "Bob",
        }
        mock_graph.list_nodes = AsyncMock(return_value=[existing_record])
        mock_graph.get_edges = AsyncMock(return_value=[])

        result = await svc.provision_new_user(
            oauth_subject="github:456",
            email="bob@example.com",
            display_name="Bob",
            provider="github",
        )
        assert result.user_id == "USER-existing-abc"


# ---------------------------------------------------------------------------
# provision_new_user — rollback on workspace creation failure
# ---------------------------------------------------------------------------


class TestProvisionRollback:
    @pytest.mark.asyncio
    async def test_rollback_deletes_user_node_when_workspace_creation_fails(
        self, mock_graph, mock_storage, mock_jwt
    ):
        """If WorkspaceNode creation raises, UserNode must be deleted."""
        # First call (UserNode creation) succeeds; second call (WorkspaceNode) raises
        mock_graph.create_node = AsyncMock(
            side_effect=[None, RuntimeError("db error")]
        )

        svc = UserProvisioningService(mock_graph, mock_storage, mock_jwt)

        with pytest.raises(RuntimeError):
            await svc.provision_new_user(
                oauth_subject="google:789",
                email="carol@example.com",
                display_name="Carol",
                provider="google",
            )

        # delete_node must have been called for the UserNode
        assert mock_graph.delete_node.called

    @pytest.mark.asyncio
    async def test_rollback_deletes_s3_prefix_when_workspace_creation_fails(
        self, mock_graph, mock_storage, mock_jwt
    ):
        """If WorkspaceNode creation raises, S3 prefix must be deleted."""
        mock_graph.create_node = AsyncMock(
            side_effect=[None, RuntimeError("db error")]
        )

        svc = UserProvisioningService(mock_graph, mock_storage, mock_jwt)

        with pytest.raises(RuntimeError):
            await svc.provision_new_user(
                oauth_subject="google:789",
                email="carol@example.com",
                display_name="Carol",
                provider="google",
            )

        # storage.delete must have been called for the S3 prefix
        assert mock_storage.delete.called
        deleted_key = mock_storage.delete.call_args[0][0]
        assert ".keep" in deleted_key

    @pytest.mark.asyncio
    async def test_rollback_is_lifo_order(
        self, mock_graph, mock_storage, mock_jwt
    ):
        """Rollback steps execute in LIFO order: S3 deleted before UserNode."""
        call_order: list[str] = []

        async def _create_node_side_effect(node):
            if call_order.count("create_node") == 0:
                call_order.append("create_node")
                return {}
            raise RuntimeError("workspace creation failed")

        async def _delete_side_effect(key):
            call_order.append(f"storage.delete:{key}")

        async def _delete_node_side_effect(node_id):
            call_order.append(f"graph.delete_node:{node_id}")

        mock_graph.create_node = AsyncMock(side_effect=_create_node_side_effect)
        mock_storage.delete = AsyncMock(side_effect=_delete_side_effect)
        mock_graph.delete_node = AsyncMock(side_effect=_delete_node_side_effect)

        svc = UserProvisioningService(mock_graph, mock_storage, mock_jwt)

        with pytest.raises(RuntimeError):
            await svc.provision_new_user(
                oauth_subject="google:789",
                email="dave@example.com",
                display_name="Dave",
                provider="google",
            )

        # Confirm both rollback steps ran
        storage_calls = [c for c in call_order if c.startswith("storage.delete")]
        graph_calls = [c for c in call_order if c.startswith("graph.delete_node")]
        assert len(storage_calls) >= 1
        assert len(graph_calls) >= 1

        # LIFO: S3 prefix was added after UserNode, so it should be rolled back first
        first_rollback = next(
            c for c in call_order if c.startswith("storage.delete") or c.startswith("graph.delete_node")
        )
        assert "storage.delete" in first_rollback


# ---------------------------------------------------------------------------
# deprovision_user
# ---------------------------------------------------------------------------


class TestDeprovisionUser:
    @pytest.mark.asyncio
    async def test_deprovision_deletes_s3_prefix(self, svc, mock_storage):
        await svc.deprovision_user("USER-test-dep")
        assert mock_storage.delete.called
        deleted_key = mock_storage.delete.call_args[0][0]
        assert "USER-test-dep" in deleted_key

    @pytest.mark.asyncio
    async def test_deprovision_deletes_user_node(self, svc, mock_graph):
        await svc.deprovision_user("USER-test-dep")
        # delete_node called for the user
        delete_calls = [
            str(c) for c in mock_graph.delete_node.call_args_list
        ]
        assert any("USER-test-dep" in c for c in delete_calls)

    @pytest.mark.asyncio
    async def test_deprovision_deletes_workspace_nodes_when_owns_edges_present(
        self, svc, mock_graph
    ):
        mock_graph.get_edges = AsyncMock(
            return_value=[{"target_id": "WS-test-workspace-001"}]
        )
        await svc.deprovision_user("USER-test-dep")

        delete_calls = [str(c) for c in mock_graph.delete_node.call_args_list]
        assert any("WS-test-workspace-001" in c for c in delete_calls)


# ---------------------------------------------------------------------------
# ProvisioningResult dataclass fields
# ---------------------------------------------------------------------------


class TestProvisioningResult:
    def test_result_has_all_required_fields(self):
        result = ProvisioningResult(
            user_id="USER-abc",
            workspace_id="WS-xyz",
            access_token="access",
            refresh_token="refresh",
            is_new_user=True,
        )
        assert result.user_id == "USER-abc"
        assert result.workspace_id == "WS-xyz"
        assert result.access_token == "access"
        assert result.refresh_token == "refresh"
        assert result.is_new_user is True
