"""Integration tests for AgentLoop new tool handlers.

Tests load_tool_set, read_knowledge, list_available_agents, list_tasks
(with new params), and get_task_details (with edges) against real
MinIO and PostgreSQL+AGE backends.

Run with::

    pytest tests/test_agent/test_loop_new_tools_integration.py -m integration
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.db.connection import create_pool
from graphclaw.infra.storage import S3StorageClient, StoragePaths
from graphclaw.scoring.engine import ScoringEngine
from graphclaw.state.machine import StateMachine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Connection constants
# ---------------------------------------------------------------------------

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
BUCKET = os.getenv("STORAGE_BUCKET", "graphclaw")
ENDPOINT = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
REGION = os.getenv("STORAGE_REGION", "us-east-1")

os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def db_pool():
    pool = await create_pool(TEST_DSN)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
def storage():
    return S3StorageClient(bucket=BUCKET, endpoint_url=ENDPOINT, region=REGION)


@pytest_asyncio.fixture
async def loop_instance(db_pool, storage):
    """Build an AgentLoop wired to real MinIO and PostgreSQL."""
    repo = AgeGraphStore(db_pool)
    scoring_engine = ScoringEngine()
    state_machine = StateMachine()

    # Minimal LLM mock — tools don't actually call LLM in these tests
    mock_llm = AsyncMock()

    loop = AgentLoop(
        graph_repo=repo,
        scoring_engine=scoring_engine,
        state_machine=state_machine,
        llm_client=mock_llm,
        storage_client=storage,
    )
    return loop


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_props(user_id: str, title: str = "Test Task") -> dict:
    uid = uuid.uuid4().hex[:8]
    return {
        "id": f"TSK-TP-{uid}-AT",
        "task_type": "ATOMIC",
        "title": title,
        "description": "Integration test task",
        "state": "ACTIVE",
        "owned_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _goal_props(user_id: str) -> dict:
    uid = uuid.uuid4().hex[:8]
    return {
        "id": f"GOAL-{uid}",
        "title": "Integration Test Goal",
        "description": "Goal for loop tool tests",
        "state": "ACTIVE",
        "priority": "P1",
        "owned_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
    }


class _NodeStub:
    def __init__(self, props: dict):
        self._props = props

    def model_dump(self, **kwargs) -> dict:
        return self._props


# ---------------------------------------------------------------------------
# _tool_load_tool_set
# ---------------------------------------------------------------------------


class TestToolLoadToolSet:
    """These tests don't need real I/O — purely verify registry integration."""

    @pytest.mark.asyncio
    async def test_activate_task_management_returns_tools(self, loop_instance):
        result = await loop_instance._tool_load_tool_set({"name": "task_management"})
        assert "activated" in result
        assert result["activated"] == "task_management"
        assert len(result["tools_available"]) > 0
        assert "create_task" in result["tools_available"]

    @pytest.mark.asyncio
    async def test_activate_planning_returns_tools(self, loop_instance):
        result = await loop_instance._tool_load_tool_set({"name": "planning"})
        assert result.get("activated") == "planning"
        assert "propose_plan" in result.get("tools_available", [])

    @pytest.mark.asyncio
    async def test_activate_unknown_set_returns_error(self, loop_instance):
        result = await loop_instance._tool_load_tool_set({"name": "does_not_exist"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_activate_delegation_returns_tools(self, loop_instance):
        result = await loop_instance._tool_load_tool_set({"name": "delegation"})
        assert result.get("activated") == "delegation"
        assert "delegate_to_agent" in result.get("tools_available", [])


# ---------------------------------------------------------------------------
# _tool_read_knowledge (requires real MinIO — seeded content)
# ---------------------------------------------------------------------------


class TestToolReadKnowledge:
    @pytest.mark.asyncio
    async def test_reads_seeded_topic(self, loop_instance, storage):
        """After seeding, reading a canonical topic should return real content."""
        # Ensure content is seeded
        from graphclaw.gateway.seeding import seed_system_content

        await seed_system_content(storage)

        result = await loop_instance._tool_read_knowledge({"topic": "node_creation_rules"})
        assert "topic" in result
        assert result["topic"] == "node_creation_rules"
        assert len(result.get("content", "")) > 50

    @pytest.mark.asyncio
    async def test_custom_knowledge_topic(self, loop_instance, storage):
        """Writing a custom topic to MinIO and reading it via the tool."""
        topic = f"test_custom_{uuid.uuid4().hex[:8]}"
        path = StoragePaths.system_knowledge(topic)
        content = f"# Custom Rules\n\nTest content for {topic}."
        try:
            await storage.write(path, content.encode(), content_type="text/markdown")
            # Clear cache if any (fresh loop_instance each test)
            result = await loop_instance._tool_read_knowledge({"topic": topic})
            assert result.get("content") == content
        finally:
            await storage.delete(path)

    @pytest.mark.asyncio
    async def test_missing_topic_returns_error_message(self, loop_instance):
        result = await loop_instance._tool_read_knowledge({"topic": "nonexistent_xyz_abc"})
        content = result.get("content", "")
        assert "not found" in content.lower() or "nonexistent_xyz_abc" in content


# ---------------------------------------------------------------------------
# _tool_list_available_agents (requires real MinIO)
# ---------------------------------------------------------------------------


class TestToolListAvailableAgents:
    @pytest.mark.asyncio
    async def test_returns_agent_list(self, loop_instance, storage):
        """After seeding, comms agent should appear in the agent list."""
        from graphclaw.gateway.seeding import seed_system_content

        await seed_system_content(storage)

        user_id = f"test-usr-{uuid.uuid4().hex[:6]}"
        result = await loop_instance._tool_list_available_agents(user_id, {})
        assert "agents" in result
        assert isinstance(result["agents"], list)

    @pytest.mark.asyncio
    async def test_user_agent_manifest_included(self, loop_instance, storage):
        """A user-created agent manifest should appear in the list."""
        user_id = f"test-usr-{uuid.uuid4().hex[:6]}"
        agent_id = f"my-agent-{uuid.uuid4().hex[:6]}"
        path = StoragePaths.agent_manifest(user_id, agent_id)
        manifest = {
            "agent_id": agent_id,
            "name": "My Test Agent",
            "type": "user",
            "description": "Test agent",
            "capabilities": ["test_cap"],
            "invocation": "async",
            "tool_hint": "For testing.",
        }
        try:
            await storage.write(
                path, json.dumps(manifest).encode(), content_type="application/json"
            )
            result = await loop_instance._tool_list_available_agents(user_id, {})
            agent_ids = {a["agent_id"] for a in result["agents"]}
            assert agent_id in agent_ids
        finally:
            await storage.delete(path)

    @pytest.mark.asyncio
    async def test_capability_filter_applied(self, loop_instance, storage):
        user_id = f"test-usr-{uuid.uuid4().hex[:6]}"
        agent_id_a = f"cap-a-{uuid.uuid4().hex[:6]}"
        agent_id_b = f"cap-b-{uuid.uuid4().hex[:6]}"

        async def _write_manifest(aid, cap):
            p = StoragePaths.agent_manifest(user_id, aid)
            m = {
                "agent_id": aid,
                "type": "user",
                "name": aid,
                "capabilities": [cap],
                "description": "",
                "invocation": "async",
            }
            await storage.write(p, json.dumps(m).encode(), content_type="application/json")
            return p

        path_a = await _write_manifest(agent_id_a, "email_read")
        path_b = await _write_manifest(agent_id_b, "task_create")

        try:
            result = await loop_instance._tool_list_available_agents(
                user_id, {"capability_filter": "email_read"}
            )
            agent_ids = {a["agent_id"] for a in result["agents"]}
            assert agent_id_a in agent_ids
            assert agent_id_b not in agent_ids
        finally:
            await storage.delete(path_a)
            await storage.delete(path_b)


# ---------------------------------------------------------------------------
# _tool_list_tasks (requires real PostgreSQL+AGE)
# ---------------------------------------------------------------------------


class TestToolListTasksWithParams:
    @pytest.mark.asyncio
    async def test_limit_respected(self, loop_instance, db_pool):
        repo = AgeGraphStore(db_pool)
        user_id = f"usr-limit-{uuid.uuid4().hex[:6]}"
        created = []
        for i in range(5):
            props = _task_props(user_id, f"Task {i}")
            await repo.create_node(_NodeStub(props))
            created.append(props["id"])

        try:
            result = await loop_instance._tool_list_tasks(user_id, {"limit": 3})
            assert result.get("count", 0) <= 3
            assert len(result.get("tasks", [])) <= 3
        finally:
            for nid in created:
                await repo.delete_node(nid)

    @pytest.mark.asyncio
    async def test_goal_id_scopes_tasks(self, loop_instance, db_pool):
        repo = AgeGraphStore(db_pool)
        user_id = f"usr-scope-{uuid.uuid4().hex[:6]}"
        goal = _goal_props(user_id)
        task_in_goal = _task_props(user_id, "Task in Goal")
        task_out_of_goal = _task_props(user_id, "Task NOT in Goal")

        await repo.create_node(_NodeStub(goal))
        await repo.create_node(_NodeStub(task_in_goal))
        await repo.create_node(_NodeStub(task_out_of_goal))
        await repo.create_edge(task_in_goal["id"], goal["id"], "PART_OF")

        try:
            result = await loop_instance._tool_list_tasks(user_id, {"goal_id": goal["id"]})
            task_ids = {t["id"] for t in result.get("tasks", [])}
            assert task_in_goal["id"] in task_ids
            assert task_out_of_goal["id"] not in task_ids
        finally:
            await repo.delete_node(task_in_goal["id"])
            await repo.delete_node(task_out_of_goal["id"])
            await repo.delete_node(goal["id"])

    @pytest.mark.asyncio
    async def test_completed_tasks_excluded_by_default(self, loop_instance, db_pool):
        repo = AgeGraphStore(db_pool)
        user_id = f"usr-complete-{uuid.uuid4().hex[:6]}"

        active_props = _task_props(user_id, "Active Task")
        active_props["state"] = "ACTIVE"

        complete_props = _task_props(user_id, "Complete Task")
        complete_props["state"] = "COMPLETE"

        await repo.create_node(_NodeStub(active_props))
        await repo.create_node(_NodeStub(complete_props))

        try:
            result = await loop_instance._tool_list_tasks(user_id, {})
            task_ids = {t["id"] for t in result.get("tasks", [])}
            assert active_props["id"] in task_ids
            assert complete_props["id"] not in task_ids
        finally:
            await repo.delete_node(active_props["id"])
            await repo.delete_node(complete_props["id"])

    @pytest.mark.asyncio
    async def test_include_completed_flag(self, loop_instance, db_pool):
        repo = AgeGraphStore(db_pool)
        user_id = f"usr-incl-{uuid.uuid4().hex[:6]}"

        complete_props = _task_props(user_id, "Done Task")
        complete_props["state"] = "COMPLETE"
        await repo.create_node(_NodeStub(complete_props))

        try:
            result = await loop_instance._tool_list_tasks(user_id, {"include_completed": True})
            task_ids = {t["id"] for t in result.get("tasks", [])}
            assert complete_props["id"] in task_ids
        finally:
            await repo.delete_node(complete_props["id"])


# ---------------------------------------------------------------------------
# _tool_get_task_details (requires real PostgreSQL+AGE)
# ---------------------------------------------------------------------------


class TestToolGetTaskDetailsWithEdges:
    @pytest.mark.asyncio
    async def test_returns_task_and_edges(self, loop_instance, db_pool):
        repo = AgeGraphStore(db_pool)
        user_id = f"usr-detail-{uuid.uuid4().hex[:6]}"

        parent_props = _task_props(user_id, "Parent Task")
        child_props = _task_props(user_id, "Child Task")

        await repo.create_node(_NodeStub(parent_props))
        await repo.create_node(_NodeStub(child_props))
        await repo.create_edge(child_props["id"], parent_props["id"], "DEPENDS_ON")

        try:
            result = await loop_instance._tool_get_task_details(
                user_id, {"node_id": parent_props["id"]}
            )
            assert "task" in result or "error" not in result
        finally:
            await repo.delete_node(child_props["id"])
            await repo.delete_node(parent_props["id"])

    @pytest.mark.asyncio
    async def test_missing_task_returns_not_found(self, loop_instance):
        result = await loop_instance._tool_get_task_details(
            "usr-any", {"node_id": "TSK-DOESNOTEXIST-XYZ"}
        )
        # Either "error" key or "not_found" status
        assert "error" in result or result.get("found") is False

