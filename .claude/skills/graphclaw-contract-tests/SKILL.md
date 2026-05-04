---
name: graphclaw-contract-tests
description: Contract test patterns for GraphClaw using schemathesis — validating the live FastAPI app against its own OpenAPI spec. Use when writing tests in tests/contract/ or checking OpenAPI spec conformance.
---

# GraphClaw Contract Test Patterns

## When to use
Writing files under `tests/contract/`. These tests run against the live app (requires Docker stack) and verify the backend does not drift from its own OpenAPI spec.

---

## File header template (L3 Contract)

```python
"""
GC-K-API-<W>-<NNN> — OpenAPI contract: <area>

Scenario: Schemathesis generates requests from the OpenAPI spec and verifies
the app responds with documented status codes and response shapes.

PRD: docs/prd/01-api-reference.md
Build wave: W<NN>
Layer: L3 Contract
Owner: backend-team
Last reviewed: YYYY-MM-DD

Cases covered:
- All endpoints respond with documented status codes
- Response bodies validate against spec schemas
- Auth-required endpoints return 401 when no token
"""
```

---

## Schemathesis state-machine pattern

```python
# tests/contract/test_openapi_schema.py
import schemathesis
from schemathesis.specs.openapi.links import OpenAPILink

schema = schemathesis.from_uri("http://localhost:8000/openapi.json")

@schema.parametrize()
def test_api_spec_conformance(case):
    """Every generated request must return a documented response."""
    response = case.call()
    case.validate_response(response)
```

Run: `pytest tests/contract/ --run-integration`  (requires Docker stack up)

---

## Adding targeted contract tests

For specific endpoints where you want tighter control:

```python
@schema.parametrize(endpoint="/app/v1/tasks")
def test_tasks_endpoint(case):
    response = case.call_and_validate()
    assert response.status_code in (200, 201, 400, 401, 422)
```

---

## Dependencies

Add to `pyproject.toml` test extras:
```toml
[project.optional-dependencies]
test = [
    ...
    "schemathesis>=3.25.0",
]
```

---

## Inventory

Add to `tests/contract/inventory.md` and run `python scripts/regen_inventory.py`.
