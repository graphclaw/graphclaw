# Testing — graphclaw

Quick reference for running tests. For the full strategy, pyramid, and conventions see [docs/testing/master-strategy.md](docs/testing/master-strategy.md).

## Commands

| Command | What it runs |
|---|---|
| `pytest tests/` | Unit tests (all layers except integration, evals, load) |
| `pytest tests/ --run-integration` | Unit + integration (requires live Docker stack) |
| `pytest tests/contract/` | Contract tests via schemathesis |
| `pytest tests/test_cli/` | Typer CLI command tests |
| `pytest tests/agent_evals/ -m eval_canary --run-evals` | Agent eval canary (3–5 fast scenarios) |
| `pytest tests/agent_evals/ --run-evals` | Full agent eval suite (slow + has LLM cost) |

## Running the Docker stack (required for integration + E2E)

```bash
docker compose up -d
# Wait for healthy:
docker compose ps
```

## Test layers at a glance

```
L1 Unit          tests/test_*/        pytest, fakes, no I/O
L3 Contract      tests/contract/      schemathesis vs /openapi.json
L4 Integration   tests/integration/   httpx + real services, --run-integration
L6 Load          tests/load/          Locust
L7 Agent Evals   tests/agent_evals/   YAML scenarios + real LLM, --run-evals
CLI              tests/test_cli/      Typer CliRunner
Manual smoke     scripts/api_smoke.py NEVER in CI
```

## Adding a test

See [docs/testing/contributing-tests.md](docs/testing/contributing-tests.md) for the decision tree (which layer → where the file goes → how to allocate a test ID → what the file header must contain).

## Coverage

```bash
pytest --cov=graphclaw --cov-report=html
open htmlcov/index.html
```

CI gate: **60% lines/branches/functions** (raises each release).
