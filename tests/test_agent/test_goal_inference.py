# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for bottom-up goal inference lifecycle in MainOrchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator
from graphclaw.infra.storage import StoragePaths
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import GoalOrigin, TaskState, TaskType
from graphclaw.models.nodes import TaskNode, Timeline
from tests.test_api.conftest import FakeGraphStore, FakeStorageClient


def _make_loop(
    repo: FakeGraphStore | None = None,
    storage: FakeStorageClient | None = None,
) -> tuple[MainOrchestrator, FakeGraphStore, FakeStorageClient]:
    graph_repo = repo or FakeGraphStore()
    storage_client = storage or FakeStorageClient()
    loop = MainOrchestrator(
        graph_repo=graph_repo,
        scoring_engine=AsyncMock(),
        state_machine=AsyncMock(),
        llm_client=None,
        storage_client=storage_client,
    )
    return loop, graph_repo, storage_client


async def _seed_clustered_tasks(repo: FakeGraphStore, user_id: str) -> list[str]:
    now = datetime.now(timezone.utc)
    ids: list[str] = []
    for idx in range(3):
        task_id = generate_task_id("AG", TaskType.ATOMIC)
        ids.append(task_id)
        task = TaskNode(
            id=task_id,
            task_type=TaskType.ATOMIC,
            title=f"Website launch task {idx + 1}",
            description="Clustered task for inferred-goal tests",
            created_by=user_id,
            owned_by=user_id,
            assigned_to="RES-launch-owner",
            state=TaskState.ACTIVE,
            timeline=Timeline(deadline=now + timedelta(days=7 + idx)),
            tags=["launch", "website"],
            created_at=now,
            updated_at=now,
        )
        await repo.create_node(task)
    return ids


@pytest.mark.asyncio
async def test_propose_goal_inference_persists_draft() -> None:
    user_id = "USER-infer-001"
    loop, repo, storage = _make_loop()
    await _seed_clustered_tasks(repo, user_id)

    result = await loop._tool_propose_goal_inference(
        user_id,
        {"min_cluster_size": 2, "max_proposals": 2},
    )

    assert result["count"] >= 1
    proposal = result["proposals"][0]
    inference_id = proposal["inference_id"]

    path = f"{StoragePaths.agent_root(user_id, 'main')}state/pending_goal_inferences/{inference_id}.json"
    raw = await storage.read(path)
    persisted = json.loads(raw.decode())

    assert persisted["status"] == "DRAFT"
    assert persisted["proposal"]["origin"] == "AGENT_INFERRED"
    assert persisted["proposal"]["confirmed_by_user"] is False
    assert len(persisted["task_ids"]) >= 2


@pytest.mark.asyncio
async def test_approve_goal_inference_creates_goal_and_part_of_edges() -> None:
    user_id = "USER-infer-002"
    loop, repo, _storage = _make_loop()
    task_ids = await _seed_clustered_tasks(repo, user_id)

    proposed = await loop._tool_propose_goal_inference(
        user_id,
        {"min_cluster_size": 2, "max_proposals": 1},
    )
    inference_id = proposed["proposals"][0]["inference_id"]

    approved = await loop._tool_approve_goal_inference(
        user_id,
        {"inference_id": inference_id},
    )

    assert approved["status"] == "APPROVED"
    goal_id = approved["goal_id"]

    goal = repo._nodes[goal_id]
    assert goal["origin"] == GoalOrigin.AGENT_INFERRED.value
    assert goal["confirmed_by_user"] is True
    assert set(goal["inferred_from"]).issuperset(set(task_ids))

    part_of_edges = [
        edge
        for edge in repo._edges
        if edge.get("edge_type") == "PART_OF" and edge.get("target_id") == goal_id
    ]
    assert len(part_of_edges) >= 2
