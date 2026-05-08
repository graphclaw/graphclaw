# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.inbound.resolver — TaskResolver resolution pipeline.

Description
-----------
Verifies regex task ID extraction, ID-based resolution with and without a
GraphRepository mock, no-match fallback, and vector search confidence
assignment at HIGH, MEDIUM, and below-threshold levels.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.inbound.resolver import TASK_ID_REGEX, TaskResolver
from graphclaw.models.enums import ConfidenceLevel, MatchedBy

# ---------------------------------------------------------------------------
# _extract_task_id
# ---------------------------------------------------------------------------


def test_extract_task_id_from_text() -> None:
    """Regex should find a valid task ID embedded in a sentence."""
    resolver = TaskResolver()
    result = resolver._extract_task_id("Please update TSK-AB-1234-ATM with new status.")
    assert result == "TSK-AB-1234-ATM"


def test_extract_task_id_returns_none() -> None:
    """Returns None when no task ID is present."""
    resolver = TaskResolver()
    result = resolver._extract_task_id("No task ID in this message at all.")
    assert result is None


def test_extract_task_id_multiple_picks_first() -> None:
    """When multiple IDs are present, the first match is returned."""
    resolver = TaskResolver()
    text = "TSK-AA-0001-DEL and TSK-BB-0002-FLW are both mentioned."
    result = resolver._extract_task_id(text)
    assert result == "TSK-AA-0001-DEL"


def test_extract_task_id_subject_and_body() -> None:
    """Task ID in the subject should be found when body+subject are combined."""
    resolver = TaskResolver()
    result = resolver._extract_task_id(" Re: TSK-CD-5678-MIL update")
    assert result == "TSK-CD-5678-MIL"


def test_extract_task_id_all_type_codes() -> None:
    """All valid type codes should match."""
    resolver = TaskResolver()
    for code in ("DEL", "ATM", "FLW", "CMP", "APR", "MIL", "RVW", "REC", "DEC", "CHK", "RES"):
        text = f"TSK-XX-1000-{code}"
        assert resolver._extract_task_id(text) == text


# ---------------------------------------------------------------------------
# resolve — with task ID match
# ---------------------------------------------------------------------------


async def test_resolve_with_task_id_match() -> None:
    """resolve() should return HIGH confidence when repo confirms task exists."""
    mock_repo = AsyncMock()
    mock_repo.get_node.return_value = {"id": "TSK-AB-0001-ATM", "title": "Test task"}

    resolver = TaskResolver(graph_repo=mock_repo)
    result = await resolver.resolve("Update on TSK-AB-0001-ATM: done!", subject="")

    assert result.task_id == "TSK-AB-0001-ATM"
    assert result.matched_by == MatchedBy.TASK_ID
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.score == 1.0
    mock_repo.get_node.assert_called_once_with("TSK-AB-0001-ATM")


async def test_resolve_with_task_id_no_repo() -> None:
    """resolve() should return 0.95 score when no repo is provided."""
    resolver = TaskResolver()
    result = await resolver.resolve("TSK-XY-9999-RES is done.")

    assert result.task_id == "TSK-XY-9999-RES"
    assert result.matched_by == MatchedBy.TASK_ID
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.score == pytest.approx(0.95)


async def test_resolve_with_task_id_repo_returns_none() -> None:
    """When repo returns None (task not found), still returns 0.95 from ID match."""
    mock_repo = AsyncMock()
    mock_repo.get_node.return_value = None

    resolver = TaskResolver(graph_repo=mock_repo)
    result = await resolver.resolve("TSK-AB-0002-FLW status check")

    assert result.task_id == "TSK-AB-0002-FLW"
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.score == pytest.approx(0.95)


async def test_resolve_with_task_id_repo_raises() -> None:
    """When repo raises, resolver falls back to unverified 0.95 ID match."""
    mock_repo = AsyncMock()
    mock_repo.get_node.side_effect = RuntimeError("db error")

    resolver = TaskResolver(graph_repo=mock_repo)
    result = await resolver.resolve("TSK-AB-0003-APR needs attention")

    assert result.task_id == "TSK-AB-0003-APR"
    assert result.score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# resolve — no match
# ---------------------------------------------------------------------------


async def test_resolve_no_match_no_pool() -> None:
    """resolve() returns empty TaskResolution when no ID found and no pool."""
    resolver = TaskResolver()
    result = await resolver.resolve("Hello, please check on the project.")

    assert result.task_id is None
    assert result.matched_by is None
    assert result.score == 0.0


async def test_resolve_embedding_unavailable_returns_manual_candidates() -> None:
    """When embedding search is unavailable, resolver should fail-open with candidates."""
    mock_repo = AsyncMock()
    mock_repo.get_node = AsyncMock(return_value=None)
    mock_repo.list_nodes_by_user = AsyncMock(
        return_value=[
            {
                "id": "TSK-AA-1001-ATM",
                "title": "Deploy API service",
                "description": "Deploying API to production",
                "state": "IN_PROGRESS",
                "node_type": "TaskNode",
            },
            {
                "id": "TSK-AA-1002-ATM",
                "title": "Prepare release notes",
                "description": "Write notes",
                "state": "PENDING",
                "node_type": "TaskNode",
            },
        ]
    )

    resolver = TaskResolver(graph_repo=mock_repo)
    result = await resolver.resolve(
        "Can you check status of API deployment",
        subject="API deployment",
        user_id="USER-1",
    )

    assert result.task_id is None
    assert result.match_unavailable_reason == "embedding_service_unavailable"
    assert len(result.candidate_nodes) >= 1
    assert result.candidate_nodes[0].node_id == "TSK-AA-1001-ATM"
    mock_repo.list_nodes_by_user.assert_called_once_with("TaskNode", "USER-1")


# ---------------------------------------------------------------------------
# resolve — vector search
# ---------------------------------------------------------------------------


async def test_resolve_vector_search_high_confidence() -> None:
    """Vector search match with similarity >= 0.7 → HIGH confidence."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_row = {"node_id": "TSK-VV-1111-DEL", "similarity": 0.85}
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    mock_pool.connection = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    mock_embedding_client = AsyncMock()
    mock_embedding_client.embed = AsyncMock(return_value=[0.1] * 1536)
    mock_repo = AsyncMock()
    mock_repo.get_node = AsyncMock(return_value={"title": "Deploy service"})

    resolver = TaskResolver(
        pool=mock_pool, graph_repo=mock_repo, embedding_client=mock_embedding_client
    )
    result = await resolver.resolve("Deploy the new service to production")

    assert result.task_id == "TSK-VV-1111-DEL"
    assert result.matched_by == MatchedBy.VECTOR_SEARCH
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.score == pytest.approx(0.85)
    assert result.matched_text == "Deploy service"


async def test_resolve_vector_search_medium_confidence() -> None:
    """Vector search match with 0.4 <= similarity < 0.7 → MEDIUM confidence."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_row = {"node_id": "TSK-MM-2222-ATM", "similarity": 0.55}
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    mock_pool.connection = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    mock_embedding_client = AsyncMock()
    mock_embedding_client.embed = AsyncMock(return_value=[0.1] * 1536)

    resolver = TaskResolver(pool=mock_pool, embedding_client=mock_embedding_client)
    result = await resolver.resolve("Some vaguely related update message")

    assert result.task_id == "TSK-MM-2222-ATM"
    assert result.matched_by == MatchedBy.VECTOR_SEARCH
    assert result.confidence == ConfidenceLevel.MEDIUM
    assert result.score == pytest.approx(0.55)


async def test_resolve_vector_search_below_threshold() -> None:
    """Vector search match below 0.4 → unmatched TaskResolution."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_row = {"task_id": "TSK-LL-3333-CHK", "title": "Low match task", "similarity": 0.25}
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    mock_pool.connection = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    resolver = TaskResolver(pool=mock_pool)
    result = await resolver.resolve("Completely unrelated message")

    assert result.task_id is None
    assert result.matched_by is None
    assert result.score == 0.0


async def test_resolve_vector_search_no_row() -> None:
    """Vector search returning None row → unmatched TaskResolution."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool.connection = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    resolver = TaskResolver(pool=mock_pool)
    result = await resolver.resolve("Message with no database match at all")

    assert result.task_id is None


async def test_resolve_vector_search_pool_raises() -> None:
    """Vector search pool exception → graceful unmatched TaskResolution."""
    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(side_effect=RuntimeError("pool error"))

    resolver = TaskResolver(pool=mock_pool)
    result = await resolver.resolve("Some message text")

    assert result.task_id is None


# ---------------------------------------------------------------------------
# TASK_ID_REGEX module-level constant
# ---------------------------------------------------------------------------


def test_task_id_regex_full_match() -> None:
    """TASK_ID_REGEX should match a standalone valid task ID."""
    assert TASK_ID_REGEX.search("TSK-AB-1234-ATM") is not None


def test_task_id_regex_no_match_partial() -> None:
    """TASK_ID_REGEX should not match an ID with an invalid type code."""
    assert TASK_ID_REGEX.search("TSK-AB-1234-XYZ") is None
