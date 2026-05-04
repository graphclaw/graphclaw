---
name: graphclaw-pytest-patterns
description: Core pytest conventions for GraphClaw — file headers, test IDs, fixture patterns, fakes-over-mocks, async setup, and inventory workflow. Use when writing or reviewing ANY pytest test in the graphclaw repo.
---

# GraphClaw pytest Patterns

## When to use this skill
Any time you write, review, or modify a pytest test file in `graphclaw/tests/`. This is the spine skill — all other test skills extend it.

---

## Mandatory file header

Every test file MUST start with this block before any import or function. Fill ALL fields.

```python
"""
GC-<L>-<DOM>-<W>-<NNN> — <One-line title>

Scenario: <1-3 sentences describing what the test proves from a user/system perspective.
Not what the code does — what the observable behaviour is.>

PRD: <docs/prd/NN-name.md §AC-N.N.N>, <second ref if needed>
Build wave: W<NN>
Layer: <L1 Unit | L3 Contract | L4 Integration | L7 Agent Eval | CLI>
Owner: <backend-team | agent-team>
Last reviewed: YYYY-MM-DD

Cases covered:
- <First test function description>
- <Second test function description>
- ...

Notes:
- <Any non-obvious setup, flags required, or constraints>
"""
```

**Rules:**
- ID format: `GC-[ULKIASLE]-[A-Z]{2,4}-W\d+-\d{3}`
- `Cases covered` list must match the actual `def test_*` functions in the file — update both together
- `Last reviewed` must be today's date when the file is modified

---

## Test ID allocation

1. Determine Layer code: `U` unit · `K` contract · `I` integration · `A` agent eval · `L` load · `CLI` → use `U` for CLI tests
2. Determine Domain code from `docs/testing/master-strategy.md#test-id-scheme`
3. Find build wave from `build-plan.md` or nearest predecessor
4. Check the relevant `inventory.md` for the last used `NNN` in that (L, DOM, W)
5. Assign the next `NNN`

---

## Fakes over mocks

Use fake implementations for boundary types. Mocks (`Mock()`, `MagicMock()`) only for third-party libraries with no fake in the repo.

```python
from tests.fixtures.fakes import FakeGraphStore, FakeStorageClient, FakeSecretsClient

async def test_task_creation_stores_node(fake_graph: FakeGraphStore):
    svc = TaskService(graph=fake_graph)
    task = await svc.create(title="Ship it", goal_id="g-001")
    node = await fake_graph.get_node(task.id)
    assert node["properties"]["title"] == "Ship it"
```

FastAPI dependency override pattern:
```python
@pytest.fixture
def app_with_fakes(fake_graph, fake_storage):
    from graphclaw.main import app
    app.dependency_overrides[get_graph_store] = lambda: fake_graph
    app.dependency_overrides[get_storage_client] = lambda: fake_storage
    yield app
    app.dependency_overrides.clear()
```

---

## Async setup (asyncio_mode = "auto")

`pyproject.toml` has `asyncio_mode = "auto"` — every `async def test_*` runs automatically. No `@pytest.mark.asyncio` needed.

Session-scoped async fixtures require the session-scoped `event_loop` fixture from root `conftest.py`:
```python
@pytest.fixture(scope="session")
async def db_pool():
    pool = await create_pool(dsn=TEST_DB_DSN)
    yield pool
    await pool.close()
```

Windows: root `conftest.py` sets `SelectorEventLoop` policy for psycopg3 compatibility — do not override this.

---

## Factory functions (`tests/fixtures/factories.py`)

```python
from tests.fixtures.factories import make_task, make_goal, make_user

task = make_task(title="Custom", priority=8)   # all fields have defaults
goal = make_goal()
user = make_user(email="test@example.com")
```

Factories return Pydantic model instances ready to pass to service functions.

---

## Integration test gate

Integration tests require the `--run-integration` flag. Gate with:
```python
pytestmark = pytest.mark.integration
```

The `integration_precheck.py` service check runs automatically when `--run-integration` is passed. If Postgres/Redis/MinIO is unreachable, tests skip (not fail).

---

## Inventory workflow

After writing the file header and tests:

1. Add row to the relevant `tests/<category>/inventory.md`:
   ```
   | GC-I-API-W11-007 | Task create persists to graph and audit | [test_task_lifecycle.py](test_task_lifecycle.py) |
   ```
2. Or regenerate automatically: `python scripts/regen_inventory.py`

---

## Quality gate before commit

```bash
ruff check --fix src/ tests/ && ruff format src/ tests/
pytest tests/           # must be green
python scripts/check_test_headers.py   # header lint must pass
```
