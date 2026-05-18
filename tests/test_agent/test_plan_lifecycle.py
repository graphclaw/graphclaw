# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for draft->approval->execution plan lifecycle in MainOrchestrator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator
from graphclaw.infra.storage import StoragePaths
from tests.test_api.conftest import FakeGraphStore, FakeStorageClient


class FailingGraphStore(FakeGraphStore):
    """Fake graph store that fails node creation for a configured title."""

    def __init__(self, fail_on_title: str) -> None:
        super().__init__()
        self._fail_on_title = fail_on_title

    async def create_node(self, node) -> dict:
        payload = node.model_dump(mode="json") if hasattr(node, "model_dump") else dict(node)
        if payload.get("title") == self._fail_on_title:
            raise RuntimeError(f"forced-create-failure:{self._fail_on_title}")
        return await super().create_node(node)


def _make_loop(
    *,
    llm_output: str,
    repo: FakeGraphStore | None = None,
    storage: FakeStorageClient | None = None,
) -> tuple[MainOrchestrator, FakeGraphStore, FakeStorageClient]:
    graph_repo = repo or FakeGraphStore()
    storage_client = storage or FakeStorageClient()

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=SimpleNamespace(content=llm_output))

    loop = MainOrchestrator(
        graph_repo=graph_repo,
        scoring_engine=AsyncMock(),
        state_machine=AsyncMock(),
        llm_client=llm,
        storage_client=storage_client,
    )
    return loop, graph_repo, storage_client


@pytest.mark.asyncio
async def test_propose_plan_persists_draft_plan() -> None:
    loop, _repo, storage = _make_loop(
        llm_output=json.dumps(
            {
                "goal_title": "Ship feature",
                "goal_description": "Ship N-016",
                "tasks": [
                    {
                        "title": "Design",
                        "task_type": "atomic",
                        "description": "Write design",
                        "depends_on_indices": [],
                        "can_be_automated": False,
                    }
                ],
                "execution_summary": "One task for test",
            }
        )
    )

    user_id = "USER-plan-001"
    result = await loop._tool_propose_plan(user_id, {"description": "Implement plan lifecycle"})

    assert result["status"] == "draft — awaiting user review and approval"
    plan_id = result["plan_id"]

    path = f"{StoragePaths.agent_root(user_id, 'main')}state/pending_plans/{plan_id}.json"
    raw = await storage.read(path)
    persisted = json.loads(raw.decode())

    assert persisted["status"] == "DRAFT"
    assert persisted["revision"] == 1
    assert persisted["tasks"][0]["draft_task_id"] == "DRAFT-TASK-1"


@pytest.mark.asyncio
async def test_execute_requires_approval_then_executes() -> None:
    loop, repo, _storage = _make_loop(
        llm_output=json.dumps(
            {
                "goal_title": "Launch",
                "goal_description": "Launch rollout",
                "tasks": [
                    {
                        "title": "Task A",
                        "task_type": "atomic",
                        "description": "First",
                        "depends_on_indices": [],
                        "can_be_automated": False,
                    },
                    {
                        "title": "Task B",
                        "task_type": "atomic",
                        "description": "Second",
                        "depends_on_indices": [0],
                        "can_be_automated": False,
                    },
                ],
                "execution_summary": "Two-step",
            }
        )
    )

    user_id = "USER-plan-002"
    proposed = await loop._tool_propose_plan(user_id, {"description": "Launch work"})
    plan_id = proposed["plan_id"]

    pre = await loop._tool_execute_plan(user_id, {"plan_id": plan_id})
    assert "Call approve_plan" in pre["error"]

    approved = await loop._tool_approve_plan(user_id, {"plan_id": plan_id})
    assert approved["status"] == "APPROVED"

    executed = await loop._tool_execute_plan(user_id, {"plan_id": plan_id})
    assert executed["status"] == "EXECUTED", executed
    assert executed["total_created"] == 2

    assert executed["goal_id"] in repo._nodes
    for task in executed["created_tasks"]:
        assert task["task_id"] in repo._nodes


@pytest.mark.asyncio
async def test_editing_approved_plan_resets_to_draft() -> None:
    loop, _repo, _storage = _make_loop(
        llm_output=json.dumps(
            {
                "goal_title": "Original Goal",
                "goal_description": "Original",
                "tasks": [],
                "execution_summary": "Original summary",
            }
        )
    )

    user_id = "USER-plan-003"
    proposed = await loop._tool_propose_plan(user_id, {"description": "Prepare goal"})
    plan_id = proposed["plan_id"]

    await loop._tool_approve_plan(user_id, {"plan_id": plan_id})
    edited = await loop._tool_edit_plan(user_id, {"plan_id": plan_id, "goal_title": "Edited Goal"})

    assert edited["status"] == "DRAFT"
    assert edited["revision"] == 2

    execute_result = await loop._tool_execute_plan(user_id, {"plan_id": plan_id})
    assert "Call approve_plan" in execute_result["error"]


@pytest.mark.asyncio
async def test_execute_rolls_back_on_failure() -> None:
    repo = FailingGraphStore(fail_on_title="Task B")
    loop, graph_repo, _storage = _make_loop(
        llm_output=json.dumps(
            {
                "goal_title": "Rollback Goal",
                "goal_description": "Rollback test",
                "tasks": [
                    {
                        "title": "Task A",
                        "task_type": "atomic",
                        "description": "First",
                        "depends_on_indices": [],
                        "can_be_automated": False,
                    },
                    {
                        "title": "Task B",
                        "task_type": "atomic",
                        "description": "Second",
                        "depends_on_indices": [0],
                        "can_be_automated": False,
                    },
                ],
                "execution_summary": "Rollback path",
            }
        ),
        repo=repo,
    )

    user_id = "USER-plan-004"
    proposed = await loop._tool_propose_plan(user_id, {"description": "Should rollback"})
    plan_id = proposed["plan_id"]
    await loop._tool_approve_plan(user_id, {"plan_id": plan_id})

    result = await loop._tool_execute_plan(user_id, {"plan_id": plan_id})
    assert result["status"] == "failed"
    assert result["rolled_back"] is True

    # Goal + any created tasks should be removed by rollback.
    assert graph_repo._nodes == {}
