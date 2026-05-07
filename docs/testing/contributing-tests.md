# Contributing Tests — graphclaw

## Decision tree: where does my test go?

```
Is this testing a Typer CLI command?
  └─ Yes → tests/test_cli/   (Typer CliRunner, fast, every PR)

Is this testing the orchestrator's chat behavior (real LLM)?
  └─ Yes → tests/agent_evals/prompts/  (YAML scenario, see §4 below)

Is this testing an API endpoint via HTTP?
  ├─ Should it fail the build? Yes → tests/integration/  (httpx + --run-integration)
  └─ Just a human sanity check? → scripts/api_smoke.py  (not in CI)

Is this testing a single function/class with no real I/O?
  └─ Yes → tests/test_<domain>/  (L1 Unit, fake injected via conftest)

Is this checking that the backend OpenAPI spec is internally consistent?
  └─ Yes → tests/contract/  (schemathesis)

Is this a load/throughput test?
  └─ Yes → tests/load/  (Locust scenario)
```

---

## Step-by-step: adding a new test

### 1. Pick the right directory (see tree above)

### 2. Allocate a test ID

Format: `GC-<L>-<DOM>-<W>-<NNN>`

- `<L>`: layer code (`U` `K` `I` `A` `L` `CLI`)
- `<DOM>`: domain code (see [master-strategy.md](master-strategy.md#test-id-scheme))
- `<W>`: current build wave from `build-plan.md` (or nearest predecessor)
- `<NNN>`: next available sequence number within that (L, DOM, W) — check the relevant `inventory.md`

Example: if you're adding an integration test for the inbound resolution layer in wave 14, and `GC-I-INB-W14-003` is the last entry in `tests/integration/inventory.md`, your new test is `GC-I-INB-W14-004`.

### 3. Write the file header

Every test file starts with the canonical header block. Copy the Python or TypeScript template from [master-strategy.md](master-strategy.md#file-header-convention) and fill in all fields. The `Cases covered` list must enumerate every `def test_*` / `it(...)` in the file.

### 4. Write the test

Follow the patterns in the relevant skill:
- Unit tests → `graphclaw-pytest-patterns` skill
- Integration tests → `graphclaw-integration-tests` skill
- Contract tests → `graphclaw-contract-tests` skill
- Agent evals → `graphclaw-agent-evals` skill
- CLI tests → `graphclaw-cli-tests` skill

Use fakes, not mocks, for `GraphStore`, `StorageClient`, `SecretsClient`. See [ADR-0002](adr/0002-fakes-over-mocks.md).

### 5. Register the ID in inventory.md

Add one row to the `inventory.md` at the test root (e.g., `tests/integration/inventory.md`):

```
| GC-I-INB-W14-004 | Inbound Slack message matches task by subject keyword | `test_inbound_resolution.py` |
```

Or run `python scripts/regen_inventory.py` which auto-generates from headers (the header must be written first).

### 6. Run quality gate before committing

```bash
ruff check --fix src/ tests/ && ruff format src/ tests/
pytest tests/  # unit suite must be green
python scripts/check_test_headers.py  # header lint must pass
```

---

## Agent eval scenarios (L7 special case)

Scenarios live in `tests/agent_evals/prompts/orchestrator/` or `tests/agent_evals/prompts/skills/`. Each is a YAML file, not a Python file.

Minimum viable scenario:
```yaml
id: GC-A-ORC-W12-NNN
title: One-line description
setup:
  seed_dataset: minimal_v1
  user: dev@example.com
turns:
  - user: "What the user says"
    assert:
      - tool_called: skill.invoke
      - latency_ms_under: 8000
budget:
  max_tokens: 2000
  max_cost_usd: 0.05
```

Mark 1 in 5 scenarios as `pytest.mark.eval_canary` so it runs on every relevant PR cheaply.

---

## Dos and don'ts

| Do | Don't |
|---|---|
| Use `FakeGraphStore` / `FakeStorageClient` | Use `Mock()` for boundary types |
| Use `renderWithProviders` in component tests | Render components bare without providers |
| Write the file header before writing any test function | Retroactively add the header before committing |
| Register the test ID in `inventory.md` | Leave the inventory stale |
| Run `ruff check --fix` before committing | Skip linting |
| Keep scripts in `scripts/` assertion-free | Add assertions to scripts and skip proper tests |
| Mark realistic agent eval scenarios as `eval_canary` | Mark expensive multi-turn scenarios as canary |
