---
name: graphclaw-test-patterns
description: Testing conventions for GraphClaw including pytest fixtures, DB test setup, factories, and known-answer test cases. Use when writing tests for any GraphClaw component.
---

# GraphClaw Test Patterns

## Directory Structure

```
tests/
  conftest.py            # Shared fixtures
  test_models/           # Pydantic model validation
  test_db/               # Graph repository + queries (integration)
  test_state/            # State machine + cascade
  test_scoring/          # Scoring factors + engine
  test_cli/              # CLI commands
  test_agent/            # Agent reasoning loop
  test_integration/      # End-to-end flows
```

## Core Fixtures (conftest.py)

```python
import pytest
import asyncio
from graphclaw.db.connection import create_pool

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def db_pool():
    """Connection pool to test database."""
    pool = await create_pool(dsn="postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw_test")
    yield pool
    await pool.close()

@pytest.fixture(autouse=True)
async def clean_graph(db_pool):
    """Reset graph state between tests."""
    async with db_pool.connection() as conn:
        await conn.execute("SELECT * FROM cypher('graphclaw', $$ MATCH (n) DETACH DELETE n $$) as (v agtype)")
    yield
```

## Factory Functions

```python
from graphclaw.models.nodes import TaskNode, GoalNode, UserNode, ResourceNode
from graphclaw.models.enums import TaskType, TaskState, GoalPriority
from datetime import datetime, timedelta

def make_task(
    task_type=TaskType.ATOMIC,
    state=TaskState.PENDING,
    title="Test Task",
    deadline_days=7,
    effort_hours=8,
    **overrides
) -> TaskNode:
    return TaskNode(
        id=f"TSK-TS-{uuid4().hex[:4].upper()}-ATM",
        task_type=task_type,
        title=title,
        description=f"Test task: {title}",
        state=state,
        timeline=Timeline(
            deadline=datetime.utcnow() + timedelta(days=deadline_days),
            estimated_effort_hours=effort_hours,
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        **overrides,
    )

def make_goal(priority=GoalPriority.P2, **overrides) -> GoalNode:
    return GoalNode(
        id=f"GOAL-{uuid4().hex[:8]}",
        title="Test Goal",
        description="Test goal description",
        priority=priority,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        **overrides,
    )

def make_user(**overrides) -> UserNode:
    return UserNode(
        id="USER-test-user",
        name="Test User",
        email="test@example.com",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        **overrides,
    )
```

## Scoring Known-Answer Tests

```python
class TestTimelineUrgency:
    def test_overdue(self):
        assert timeline_urgency(days_remaining=-1, effort=0) == 1.2

    def test_far_out(self):
        assert timeline_urgency(days_remaining=30, effort=2) == 0.2

    def test_tight_slack(self):
        # 3 days remaining, 4 days effort -> slack = -1 -> +0.30
        score = timeline_urgency(days_remaining=3, effort=4)
        assert score == 0.6 + 0.30  # base + slack

class TestCriticalPath:
    def test_on_cp_p1(self):
        assert critical_path_score(on_cp=True, priority="P1") == 1.5

    def test_off_cp(self):
        assert critical_path_score(on_cp=False, priority="P1") == 0.0
```

## State Machine Tests

```python
class TestValidTransitions:
    def test_pending_to_active(self):
        task = make_task(state=TaskState.PENDING)
        machine.transition(task, TaskState.ACTIVE, changed_by="HUMAN")
        assert task.state == TaskState.ACTIVE
        assert len(task.state_history) == 1

    def test_invalid_cancelled_to_active(self):
        task = make_task(state=TaskState.CANCELLED)
        with pytest.raises(InvalidTransitionError):
            machine.transition(task, TaskState.ACTIVE, changed_by="HUMAN")
```

## Integration Test Pattern

```python
class TestEndToEnd:
    async def test_create_score_recommend(self, db_pool):
        # 1. Create a goal with 3 tasks
        goal = make_goal(priority=GoalPriority.P1)
        t1 = make_task(title="Research", deadline_days=2)
        t2 = make_task(title="Write proposal", deadline_days=5)
        t3 = make_task(title="Review", deadline_days=7)

        # 2. Insert into graph with dependencies
        await repo.create_node(goal)
        await repo.create_node(t1)
        await repo.create_node(t2)
        await repo.create_node(t3)
        await repo.create_edge(t2, t1, EdgeType.DEPENDS_ON)  # t2 depends on t1
        await repo.create_edge(t3, t2, EdgeType.DEPENDS_ON)

        # 3. Score
        scores = await engine.score_all(user)

        # 4. Verify t1 ranks highest (earliest deadline + sequential chain)
        assert scores[0].node_id == t1.id
```

## Conventions

- Integration tests against **real Postgres+AGE in Docker** (not mocks)
- Use `pytest-asyncio` for async test functions
- Run with `pytest tests/` (from CLAUDE.md)
- Each test class focuses on one behavior
- Factory functions produce valid defaults — override only what's being tested
