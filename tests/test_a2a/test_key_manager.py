"""Tests for graphclaw.a2a.key_manager — A2AKeyManager unit tests.

All tests use a mocked ``GraphStore`` so no real database connection is required.
The mock is configured per-test to return the exact node shapes that
``A2AKeyManager`` expects from ``create_node``, ``get_node``, ``update_node``,
and ``list_nodes``.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.a2a.key_manager import KEY_PREFIX, A2AKeyManager
from graphclaw.a2a.models import A2AKeyRef, A2ARegistration

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_mock_store() -> MagicMock:
    """Return a MagicMock that satisfies the GraphStore ABC async interface."""
    store = MagicMock()
    store.create_node = AsyncMock(return_value={})
    store.get_node = AsyncMock(return_value=None)
    store.update_node = AsyncMock(return_value={})
    store.list_nodes = AsyncMock(return_value=[])
    store.delete_node = AsyncMock(return_value=None)
    return store


def _make_manager(store: MagicMock | None = None) -> A2AKeyManager:
    return A2AKeyManager(graph_store=store or _make_mock_store())


# ── generate_key ───────────────────────────────────────────────────────────────


class TestGenerateKey:
    """Tests for A2AKeyManager.generate_key()."""

    def test_plaintext_starts_with_prefix(self) -> None:
        manager = _make_manager()
        plaintext, _ = manager.generate_key()
        assert plaintext.startswith(KEY_PREFIX), (
            f"Expected key to start with {KEY_PREFIX!r}, got {plaintext!r}"
        )

    def test_plaintext_has_correct_total_length(self) -> None:
        # KEY_PREFIX = "wg_agent_" (9 chars) + 32 chars from token_urlsafe(24)
        manager = _make_manager()
        plaintext, _ = manager.generate_key()
        # secrets.token_urlsafe(24) always produces exactly 32 base64url chars
        assert len(plaintext) == len(KEY_PREFIX) + 32, f"Unexpected key length: {len(plaintext)}"

    def test_hash_is_sha256_hex(self) -> None:
        manager = _make_manager()
        plaintext, digest = manager.generate_key()
        expected = hashlib.sha256(plaintext.encode()).hexdigest()
        assert digest == expected

    def test_hash_is_64_hex_chars(self) -> None:
        manager = _make_manager()
        _, digest = manager.generate_key()
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_successive_calls_produce_different_keys(self) -> None:
        manager = _make_manager()
        key1, _ = manager.generate_key()
        key2, _ = manager.generate_key()
        assert key1 != key2, "Two successive generate_key() calls returned the same key"


# ── Constant-time comparison ───────────────────────────────────────────────────


class TestConstantTimeCompare:
    """Verify that verify_key uses hmac.compare_digest (constant-time) behaviour."""

    @pytest.mark.asyncio
    async def test_correct_key_returns_user_id(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        plaintext, digest = manager.generate_key()

        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-abc",
                    "api_key_hash": digest,
                    "user_id": "USER-xyz",
                    "resource_type": "AI_AGENT",
                }
            ]
        )

        result = await manager.verify_key(plaintext)
        assert result == "USER-xyz"

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        _, digest = manager.generate_key()

        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-abc",
                    "api_key_hash": digest,
                    "user_id": "USER-xyz",
                    "resource_type": "AI_AGENT",
                }
            ]
        )

        # A different, valid-format key that does not match
        other_plaintext, _ = manager.generate_key()
        result = await manager.verify_key(other_plaintext)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_key_returns_none(self) -> None:
        manager = _make_manager()
        result = await manager.verify_key("")
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_prefix_returns_none(self) -> None:
        manager = _make_manager()
        result = await manager.verify_key("not_a_wg_agent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_key_returns_none(self) -> None:
        """A node whose api_key_hash is None (revoked) must not authenticate."""
        store = _make_mock_store()
        manager = _make_manager(store)
        plaintext, _ = manager.generate_key()

        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-abc",
                    "api_key_hash": None,  # revoked
                    "user_id": "USER-xyz",
                    "resource_type": "AI_AGENT",
                }
            ]
        )

        result = await manager.verify_key(plaintext)
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_hmac_compare_digest(self) -> None:
        """verify_key must call hmac.compare_digest (not ==) for the hash comparison."""
        store = _make_mock_store()
        manager = _make_manager(store)
        plaintext, digest = manager.generate_key()

        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-abc",
                    "api_key_hash": digest,
                    "user_id": "USER-xyz",
                    "resource_type": "AI_AGENT",
                }
            ]
        )

        with patch(
            "graphclaw.a2a.key_manager.hmac.compare_digest", wraps=hmac.compare_digest
        ) as spy:
            await manager.verify_key(plaintext)
            spy.assert_called_once()


# ── register_agent + verify_key flow ──────────────────────────────────────────


class TestRegisterAndVerify:
    """End-to-end register → verify flow with a mocked GraphStore."""

    @pytest.mark.asyncio
    async def test_register_returns_key_ref_and_plaintext(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        registration = A2ARegistration(agent_name="TestAgent", description="CI bot")

        key_ref, plaintext = await manager.register_agent("USER-001", registration)

        assert isinstance(key_ref, A2AKeyRef)
        assert key_ref.agent_name == "TestAgent"
        assert key_ref.user_id == "USER-001"
        assert plaintext.startswith(KEY_PREFIX)
        store.create_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_registered_key_verifies_correctly(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        registration = A2ARegistration(agent_name="VerifyAgent")

        # Capture the node data passed to create_node so we can serve it back
        created: dict = {}

        async def _capture_create(node_data: dict) -> dict:
            created.update(node_data)
            return node_data

        store.create_node = AsyncMock(side_effect=_capture_create)
        key_ref, plaintext = await manager.register_agent("USER-002", registration)

        # Simulate verify_key by returning the created node from list_nodes
        store.list_nodes = AsyncMock(return_value=[created])

        user_id = await manager.verify_key(plaintext)
        assert user_id == "USER-002"

    @pytest.mark.asyncio
    async def test_create_node_called_with_api_key_hash(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        registration = A2ARegistration(agent_name="HashCheck")
        _, plaintext = None, None

        async def _capture(node_data: dict) -> dict:
            nonlocal plaintext
            return node_data

        store.create_node = AsyncMock(side_effect=_capture)
        _, plaintext = await manager.register_agent("USER-003", registration)

        call_kwargs = store.create_node.call_args[0][0]
        assert "api_key_hash" in call_kwargs
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        assert call_kwargs["api_key_hash"] == expected_hash


# ── rotate_key flow ────────────────────────────────────────────────────────────


class TestRotateKey:
    """Tests for A2AKeyManager.rotate_key()."""

    @pytest.mark.asyncio
    async def test_rotate_returns_new_plaintext(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)

        old_plaintext, old_hash = manager.generate_key()
        store.get_node = AsyncMock(
            return_value={
                "id": "RES-rotate",
                "api_key_hash": old_hash,
                "user_id": "USER-001",
                "resource_type": "AI_AGENT",
            }
        )

        new_plaintext, new_hash = await manager.rotate_key("USER-001", "RES-rotate")

        assert new_plaintext.startswith(KEY_PREFIX)
        assert new_plaintext != old_plaintext
        assert new_hash == hashlib.sha256(new_plaintext.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_rotate_calls_update_node_with_new_hash(self) -> None:
        store = _make_mock_store()
        manager = _make_manager(store)
        old_plaintext, old_hash = manager.generate_key()
        store.get_node = AsyncMock(
            return_value={
                "id": "RES-rotate2",
                "api_key_hash": old_hash,
                "user_id": "USER-001",
                "resource_type": "AI_AGENT",
            }
        )

        new_plaintext, _ = await manager.rotate_key("USER-001", "RES-rotate2")

        store.update_node.assert_called_once()
        update_args = store.update_node.call_args
        updated_data = update_args[0][1]
        expected_new_hash = hashlib.sha256(new_plaintext.encode()).hexdigest()
        assert updated_data["api_key_hash"] == expected_new_hash

    @pytest.mark.asyncio
    async def test_rotate_raises_key_error_for_missing_node(self) -> None:
        store = _make_mock_store()
        store.get_node = AsyncMock(return_value=None)
        manager = _make_manager(store)

        with pytest.raises(KeyError):
            await manager.rotate_key("USER-001", "RES-missing")

    @pytest.mark.asyncio
    async def test_rotate_raises_key_error_for_wrong_owner(self) -> None:
        store = _make_mock_store()
        store.get_node = AsyncMock(
            return_value={
                "id": "RES-other",
                "api_key_hash": "somehash",
                "user_id": "USER-different",
                "resource_type": "AI_AGENT",
            }
        )
        manager = _make_manager(store)

        with pytest.raises(KeyError):
            await manager.rotate_key("USER-attacker", "RES-other")

    @pytest.mark.asyncio
    async def test_old_key_no_longer_valid_after_rotation(self) -> None:
        """After rotation, the old plaintext must not authenticate."""
        store = _make_mock_store()
        manager = _make_manager(store)
        old_plaintext, old_hash = manager.generate_key()

        node: dict = {
            "id": "RES-rot3",
            "api_key_hash": old_hash,
            "user_id": "USER-001",
            "resource_type": "AI_AGENT",
        }
        store.get_node = AsyncMock(return_value=node)

        async def _update(node_id: str, updates: dict) -> dict:
            node.update(updates)
            return node

        store.update_node = AsyncMock(side_effect=_update)

        new_plaintext, _ = await manager.rotate_key("USER-001", "RES-rot3")

        # After rotation the node has the new hash
        store.list_nodes = AsyncMock(return_value=[node])

        # Old key must fail
        result_old = await manager.verify_key(old_plaintext)
        assert result_old is None

        # New key must succeed
        result_new = await manager.verify_key(new_plaintext)
        assert result_new == "USER-001"


# ── revoke_key flow ────────────────────────────────────────────────────────────


class TestRevokeKey:
    """Tests for A2AKeyManager.revoke_key()."""

    @pytest.mark.asyncio
    async def test_revoke_clears_api_key_hash(self) -> None:
        store = _make_mock_store()
        _, key_hash = _make_manager().generate_key()
        store.get_node = AsyncMock(
            return_value={
                "id": "RES-revoke",
                "api_key_hash": key_hash,
                "user_id": "USER-001",
                "resource_type": "AI_AGENT",
            }
        )
        manager = _make_manager(store)

        await manager.revoke_key("USER-001", "RES-revoke")

        store.update_node.assert_called_once()
        update_data = store.update_node.call_args[0][1]
        assert update_data["api_key_hash"] is None

    @pytest.mark.asyncio
    async def test_revoke_raises_key_error_for_missing_node(self) -> None:
        store = _make_mock_store()
        store.get_node = AsyncMock(return_value=None)
        manager = _make_manager(store)

        with pytest.raises(KeyError):
            await manager.revoke_key("USER-001", "RES-missing")

    @pytest.mark.asyncio
    async def test_revoke_raises_key_error_for_wrong_owner(self) -> None:
        store = _make_mock_store()
        store.get_node = AsyncMock(
            return_value={
                "id": "RES-other",
                "api_key_hash": "somehash",
                "user_id": "USER-actual-owner",
                "resource_type": "AI_AGENT",
            }
        )
        manager = _make_manager(store)

        with pytest.raises(KeyError):
            await manager.revoke_key("USER-attacker", "RES-other")

    @pytest.mark.asyncio
    async def test_revoked_key_cannot_authenticate(self) -> None:
        """After revoke_key, the plaintext must no longer verify."""
        store = _make_mock_store()
        manager = _make_manager(store)
        plaintext, key_hash = manager.generate_key()

        node: dict = {
            "id": "RES-rev2",
            "api_key_hash": key_hash,
            "user_id": "USER-001",
            "resource_type": "AI_AGENT",
        }
        store.get_node = AsyncMock(return_value=node)

        async def _update(node_id: str, updates: dict) -> dict:
            node.update(updates)
            return node

        store.update_node = AsyncMock(side_effect=_update)

        await manager.revoke_key("USER-001", "RES-rev2")

        # After revocation, api_key_hash is None — verify_key must return None
        store.list_nodes = AsyncMock(return_value=[node])

        result = await manager.verify_key(plaintext)
        assert result is None


# ── list_agents ────────────────────────────────────────────────────────────────


class TestListAgents:
    """Tests for A2AKeyManager.list_agents()."""

    @pytest.mark.asyncio
    async def test_returns_only_active_agents(self) -> None:
        store = _make_mock_store()
        now = datetime.now(UTC)
        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-1",
                    "agent_name": "ActiveBot",
                    "api_key_hash": "abc123",
                    "user_id": "USER-001",
                    "created_at": now.isoformat(),
                    "resource_type": "AI_AGENT",
                },
                {
                    "id": "RES-2",
                    "agent_name": "RevokedBot",
                    "api_key_hash": None,  # revoked
                    "user_id": "USER-001",
                    "created_at": now.isoformat(),
                    "resource_type": "AI_AGENT",
                },
            ]
        )
        manager = _make_manager(store)

        refs = await manager.list_agents("USER-001")

        assert len(refs) == 1
        assert refs[0].agent_name == "ActiveBot"
        assert refs[0].key_id == "RES-1"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_agents(self) -> None:
        store = _make_mock_store()
        store.list_nodes = AsyncMock(return_value=[])
        manager = _make_manager(store)

        refs = await manager.list_agents("USER-none")
        assert refs == []

    @pytest.mark.asyncio
    async def test_results_sorted_by_created_at(self) -> None:
        store = _make_mock_store()
        t1 = datetime(2025, 1, 1, tzinfo=UTC)
        t2 = datetime(2025, 6, 1, tzinfo=UTC)
        # Return in reverse order to test sorting
        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "RES-B",
                    "agent_name": "BotB",
                    "api_key_hash": "hash2",
                    "user_id": "USER-001",
                    "created_at": t2.isoformat(),
                    "resource_type": "AI_AGENT",
                },
                {
                    "id": "RES-A",
                    "agent_name": "BotA",
                    "api_key_hash": "hash1",
                    "user_id": "USER-001",
                    "created_at": t1.isoformat(),
                    "resource_type": "AI_AGENT",
                },
            ]
        )
        manager = _make_manager(store)

        refs = await manager.list_agents("USER-001")

        assert len(refs) == 2
        assert refs[0].agent_name == "BotA"  # earlier created_at first
        assert refs[1].agent_name == "BotB"

    @pytest.mark.asyncio
    async def test_graph_error_returns_empty_list(self) -> None:
        store = _make_mock_store()
        store.list_nodes = AsyncMock(side_effect=RuntimeError("DB offline"))
        manager = _make_manager(store)

        # Should not raise — returns empty list with a WARNING log
        refs = await manager.list_agents("USER-001")
        assert refs == []
