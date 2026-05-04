---
name: graphclaw-integration-tests
description: Integration test patterns for GraphClaw — httpx.AsyncClient against a live FastAPI app, service precheck, journey tests, and fixture teardown. Use when writing tests in tests/integration/ or tests that require real Postgres/AGE/Redis/MinIO.
---

# GraphClaw Integration Test Patterns

## When to use
Writing files under `tests/integration/` or adding `pytestmark = pytest.mark.integration` to any test. These tests require the Docker stack to be running.

---

## File header template (L4 Integration)

```python
"""
GC-I-<DOM>-<W>-<NNN> — <Title>

Scenario: <What end-to-end behaviour this proves.>

PRD: <ref>
Build wave: W<NN>
Layer: L4 Integration
Owner: backend-team
Last reviewed: YYYY-MM-DD

Cases covered:
- <case 1>
- <case 2>

Notes:
- Requires --run-integration flag and live Postgres+AGE+Redis+MinIO.
- Uses clean_graph autouse fixture — graph is reset between each test.
"""
```

---

## Base pattern

```python
import pytest
import httpx

pytestmark = pytest.mark.integration

@pytest.fixture
async def client(running_app):
    async with httpx.AsyncClient(
        app=running_app,
        base_url="http://test",
        timeout=15.0,
    ) as c:
        yield c

@pytest.fixture
async def auth_headers(client):
    r = await client.post("/auth/dev-token", json={"email": "test@example.com"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

async def test_create_task_persists_to_graph(client, auth_headers, clean_graph, fake_graph):
    payload = {"title": "Ship Q2 plan", "goal_id": "g-001", "priority": 5}
    r = await client.post("/app/v1/tasks", json=payload, headers=auth_headers)
    assert r.status_code == 201
    task_id = r.json()["id"]

    # Verify in graph directly
    node = await fake_graph.get_node(task_id)
    assert node["properties"]["title"] == "Ship Q2 plan"

    # Verify in audit
    r2 = await client.get(f"/app/v1/audit?entity_id={task_id}", headers=auth_headers)
    assert any(e["action"] == "task.created" for e in r2.json())
```

---

## Multi-step journey tests (`tests/integration/test_journeys/`)

Use for flows that span multiple endpoints and verify state accumulation:

```python
async def test_first_user_journey(client, clean_graph):
    """GC-I-JNY-W15-001 — New user signs up, creates first goal, completes a task."""
    # Step 1: sign up
    r = await client.post("/auth/dev-token", json={"email": "alice@example.com"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # Step 2: create goal
    r = await client.post("/app/v1/goals", json={"title": "Launch product"}, headers=headers)
    goal_id = r.json()["id"]

    # Step 3: create task under goal
    r = await client.post("/app/v1/tasks", json={"title": "Write spec", "goal_id": goal_id}, headers=headers)
    task_id = r.json()["id"]

    # Step 4: complete task
    r = await client.post(f"/app/v1/tasks/{task_id}/transition", json={"to": "DONE"}, headers=headers)
    assert r.status_code == 200

    # Verify final state in graph
    r = await client.get(f"/app/v1/tasks/{task_id}", headers=headers)
    assert r.json()["status"] == "DONE"
```

---

## Service precheck

If the Docker stack is not running, integration tests are **skipped**, not failed. The precheck runs automatically when `--run-integration` is passed.

To run only integration tests locally:
```bash
docker compose up -d
pytest tests/integration/ --run-integration
```

To run unit + integration together:
```bash
pytest tests/ --run-integration
```

---

## clean_graph fixture

Defined in root `tests/conftest.py`. Autouse for all tests in `tests/integration/`. Resets the AGE graph via `MATCH (n) DETACH DELETE n` before each test. Do not reset manually inside test functions.

---

## Inventory

Add to `tests/integration/inventory.md` after writing the test, or run:
```bash
python scripts/regen_inventory.py
```
