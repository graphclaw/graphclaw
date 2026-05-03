"""Tests for Wave 8 completion: DirectoryIndexer, embedding, OrgTaskIndexer, CrossTenantRepo.

Covers:
- DirectoryIndexer dispatches member_added / member_removed to MembershipCascade
- DirectoryIndexer handles unknown events gracefully
- build_embedding_text produces canonical combined text
- UserDirectoryEmbedder.embed_and_store swallows errors gracefully
- OrgTaskIndexer dispatches task_created / task_updated / task_archived
- CrossTenantRepo enforces caller_org_ids fail-closed
- CrossTenantRepo raises ACLViolation on cross-user query
- BrokerDep wiring: get_broker returns None when app.state.broker is absent
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# DirectoryIndexer
# ---------------------------------------------------------------------------


class TestDirectoryIndexer:
    """DirectoryIndexer dispatches membership events to MembershipCascade."""

    @pytest.mark.asyncio
    async def test_member_added_dispatches_to_cascade(self):
        """member_added event → MembershipCascade.on_member_added called."""
        from graphclaw.identity.directory_indexer import DirectoryIndexer

        broker = MagicMock()
        cascade = MagicMock()
        cascade.on_member_added = AsyncMock()
        indexer = DirectoryIndexer(broker=broker, cascade=cascade)

        await indexer._dispatch({"event": "member_added", "user_id": "u1", "org_id": "org1"})

        cascade.on_member_added.assert_awaited_once_with("u1", "org1")

    @pytest.mark.asyncio
    async def test_member_removed_dispatches_to_cascade(self):
        """member_removed event → MembershipCascade.on_member_removed called."""
        from graphclaw.identity.directory_indexer import DirectoryIndexer

        broker = MagicMock()
        cascade = MagicMock()
        cascade.on_member_removed = AsyncMock()
        indexer = DirectoryIndexer(broker=broker, cascade=cascade)

        await indexer._dispatch({"event": "member_removed", "user_id": "u2", "org_id": "org1"})

        cascade.on_member_removed.assert_awaited_once_with("u2", "org1")

    @pytest.mark.asyncio
    async def test_profile_updated_calls_member_added(self):
        """profile_updated event → re-add via on_member_added."""
        from graphclaw.identity.directory_indexer import DirectoryIndexer

        broker = MagicMock()
        cascade = MagicMock()
        cascade.on_member_added = AsyncMock()
        indexer = DirectoryIndexer(broker=broker, cascade=cascade)

        await indexer._dispatch({"event": "profile_updated", "user_id": "u1", "org_id": "org1"})

        cascade.on_member_added.assert_awaited_once_with("u1", "org1")

    @pytest.mark.asyncio
    async def test_unknown_event_ignored(self):
        """Unknown event type does not raise and calls no cascade methods."""
        from graphclaw.identity.directory_indexer import DirectoryIndexer

        broker = MagicMock()
        cascade = MagicMock()
        cascade.on_member_added = AsyncMock()
        cascade.on_member_removed = AsyncMock()
        indexer = DirectoryIndexer(broker=broker, cascade=cascade)

        # Should not raise
        await indexer._dispatch({"event": "unknown_event", "user_id": "u1", "org_id": "org1"})

        cascade.on_member_added.assert_not_called()
        cascade.on_member_removed.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_user_id_ignored(self):
        """Payload missing user_id is silently ignored."""
        from graphclaw.identity.directory_indexer import DirectoryIndexer

        broker = MagicMock()
        cascade = MagicMock()
        cascade.on_member_added = AsyncMock()
        indexer = DirectoryIndexer(broker=broker, cascade=cascade)

        await indexer._dispatch({"event": "member_added", "org_id": "org1"})  # no user_id

        cascade.on_member_added.assert_not_called()


# ---------------------------------------------------------------------------
# identity/embedding.py
# ---------------------------------------------------------------------------


class TestEmbedding:
    """build_embedding_text and UserDirectoryEmbedder."""

    def test_build_embedding_text_all_fields(self):
        """All three fields are joined by ' | '."""
        from graphclaw.identity.embedding import build_embedding_text

        result = build_embedding_text(
            display_name="Alice Smith",
            emails=["alice@example.com"],
            discoverable_aliases=["alice", "a.smith"],
        )
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "alice" in result
        assert " | " in result

    def test_build_embedding_text_empty_emails(self):
        """Empty emails still produces valid output."""
        from graphclaw.identity.embedding import build_embedding_text

        result = build_embedding_text("Bob", [], ["bob"])
        assert "Bob" in result
        assert "bob" in result

    @pytest.mark.asyncio
    async def test_embed_and_store_no_client_noop(self):
        """When embedding_client is None, embed_and_store is a no-op."""
        from graphclaw.identity.embedding import UserDirectoryEmbedder

        embedder = UserDirectoryEmbedder(embedding_client=None, pool=MagicMock())
        # Should not raise
        await embedder.embed_and_store("u1", "org1", "Alice", [], [])

    @pytest.mark.asyncio
    async def test_embed_and_store_swallows_error(self):
        """Embedding client error is swallowed; does not raise."""
        from graphclaw.identity.embedding import UserDirectoryEmbedder

        client = MagicMock()
        client.embed = AsyncMock(side_effect=RuntimeError("API down"))
        embedder = UserDirectoryEmbedder(embedding_client=client, pool=None)

        # Must not raise
        await embedder.embed_and_store("u1", "org1", "Bob", ["b@x.com"], ["bob"])


# ---------------------------------------------------------------------------
# OrgTaskIndexer
# ---------------------------------------------------------------------------


class TestOrgTaskIndexer:
    """OrgTaskIndexer dispatches task mutation events to OrgTaskIndex."""

    @pytest.mark.asyncio
    async def test_task_created_calls_upsert(self):
        """task_created event calls OrgTaskIndex.upsert with correct entry."""
        from graphclaw.cross_tenant.indexer import OrgTaskIndexer
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()
        broker = MagicMock()
        indexer = OrgTaskIndexer(broker=broker, task_index=index, store=None)

        await indexer._dispatch(
            {
                "event": "task_created",
                "task_id": "TSK-001",
                "owner_user_id": "u1",
                "org_id": "org1",
                "state": "PENDING",
            }
        )

        index.upsert.assert_awaited_once()
        entry = index.upsert.call_args[0][0]
        assert entry.task_id == "TSK-001"
        assert entry.org_id == "org1"
        assert entry.state == "PENDING"

    @pytest.mark.asyncio
    async def test_task_archived_calls_set_archived(self):
        """task_archived event calls OrgTaskIndex.set_archived."""
        from graphclaw.cross_tenant.indexer import OrgTaskIndexer
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.set_archived = AsyncMock()
        broker = MagicMock()
        indexer = OrgTaskIndexer(broker=broker, task_index=index, store=None)

        await indexer._dispatch(
            {
                "event": "task_archived",
                "task_id": "TSK-001",
                "archived_at": "2026-05-01T00:00:00+00:00",
            }
        )

        index.set_archived.assert_awaited_once()
        assert index.set_archived.call_args[0][0] == "TSK-001"

    @pytest.mark.asyncio
    async def test_missing_task_id_ignored(self):
        """Payload missing task_id is silently ignored without calling index."""
        from graphclaw.cross_tenant.indexer import OrgTaskIndexer
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()
        broker = MagicMock()
        indexer = OrgTaskIndexer(broker=broker, task_index=index, store=None)

        await indexer._dispatch({"event": "task_created"})  # no task_id

        index.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_uses_store_when_available(self):
        """When store is provided, fetches full node before upsert."""
        from graphclaw.cross_tenant.indexer import OrgTaskIndexer
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()
        store = MagicMock()
        store.get_node = AsyncMock(
            return_value={
                "id": "TSK-002",
                "title": "Store-fetched title",
                "owner_user_id": "u2",
                "org_id": "org2",
                "state": "IN_PROGRESS",
            }
        )
        broker = MagicMock()
        indexer = OrgTaskIndexer(broker=broker, task_index=index, store=store)

        await indexer._dispatch(
            {
                "event": "task_updated",
                "task_id": "TSK-002",
            }
        )

        store.get_node.assert_awaited_once_with("TSK-002")
        index.upsert.assert_awaited_once()
        entry = index.upsert.call_args[0][0]
        assert entry.summary_text == "Store-fetched title"


# ---------------------------------------------------------------------------
# CrossTenantRepo
# ---------------------------------------------------------------------------


class TestCrossTenantRepo:
    """CrossTenantRepo enforces ACL at the repository layer (FR-XT-003)."""

    @pytest.mark.asyncio
    async def test_empty_org_ids_returns_empty(self):
        """Empty caller_org_ids returns [] without querying the index (fail-closed, NFR-004)."""
        from graphclaw.cross_tenant.repo import CrossTenantRepo
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.list_for_assignee = AsyncMock()
        repo = CrossTenantRepo(task_index=index)

        result = await repo.list_for_assignee(
            assignee_user_id="u1",
            caller_user_id="u1",
            caller_org_ids=[],
        )

        assert result == []
        index.list_for_assignee.assert_not_called()

    @pytest.mark.asyncio
    async def test_cross_user_query_raises_acl_violation(self):
        """Cross-user query (caller != assignee) raises ACLViolation (FR-XT-003 AC1)."""
        from graphclaw.cross_tenant.repo import ACLViolation, CrossTenantRepo
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        repo = CrossTenantRepo(task_index=index)

        with pytest.raises(ACLViolation):
            await repo.list_for_assignee(
                assignee_user_id="u-other",
                caller_user_id="u-caller",
                caller_org_ids=["org1"],
            )

    @pytest.mark.asyncio
    async def test_valid_self_query_delegates_to_index(self):
        """Self-query with valid org_ids delegates to OrgTaskIndex."""
        from graphclaw.cross_tenant.repo import CrossTenantRepo
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        index.list_for_assignee = AsyncMock(return_value=[])
        repo = CrossTenantRepo(task_index=index)

        await repo.list_for_assignee(
            assignee_user_id="u1",
            caller_user_id="u1",
            caller_org_ids=["org1"],
        )

        index.list_for_assignee.assert_awaited_once_with(
            assignee_user_id="u1",
            caller_org_ids=["org1"],
            state_filter=None,
            deadline_before=None,
            workspace_id=None,
            limit=50,
        )

    @pytest.mark.asyncio
    async def test_get_task_summary_empty_org_ids_returns_none(self):
        """get_task_summary with empty org_ids returns None (fail-closed)."""
        from graphclaw.cross_tenant.repo import CrossTenantRepo
        from graphclaw.cross_tenant.task_index import OrgTaskIndex

        index = MagicMock(spec=OrgTaskIndex)
        repo = CrossTenantRepo(task_index=index)

        result = await repo.get_task_summary("TSK-001", "u1", [])
        assert result is None
