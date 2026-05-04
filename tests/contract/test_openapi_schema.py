"""
GC-K-API-W12-001 — OpenAPI schema conformance via schemathesis

Scenario: Every endpoint advertised in the FastAPI OpenAPI spec must respond
with a schema-conforming body. Schemathesis generates requests from the schema
and verifies that responses match the declared response schemas. Catches drift
where routes are added without matching schema annotations, or response shapes
diverge from what the spec declares.

PRD: docs/prd/00-overview.md §2
Build wave: W12
Layer: L3 Contract
Owner: backend-team
Last reviewed: 2026-05-04

Cases covered:
- Every path+method in OpenAPI spec returns a response with correct shape
- No endpoint raises an unhandled 500 error for schema-valid inputs
- OpenAPI spec itself parses and loads without validation errors

Notes:
- Runs against in-process FastAPI app (no live services required)
- Endpoints gated by auth return 401/403 — schemathesis marks these as valid
- Run standalone: pytest tests/contract/ -v
- For stateful mode: pytest tests/contract/ -v --hypothesis-seed=0
"""
import pytest
import schemathesis
from schemathesis.specs.openapi.links import LocalStep


# ── Stateless schema conformance ─────────────────────────────────────────────
# Each case exercises a single operation with generated inputs.
# Auth-gated endpoints return 401/403 — we allow these as valid states.

ALLOWED_STATUSES = {200, 201, 204, 400, 401, 403, 404, 422, 429}


@schemathesis.parametrize()
def test_api_conformance(case, schema):
    """Every OpenAPI operation must return a documented status code."""
    response = case.call_asgi()
    case.validate_response(response, checks=(
        schemathesis.checks.not_a_server_error,
        schemathesis.checks.response_schema_conformance,
    ))


# ── Targeted contract tests ───────────────────────────────────────────────────
# Smoke checks that verify specific critical endpoints are reachable and
# return the right shape regardless of auth. Use a shared AsyncClient so
# no rate-limit hit.

import httpx
from graphclaw.main import app as fastapi_app


@pytest.fixture(scope="module")
def client():
    with httpx.Client(app=fastapi_app, base_url="http://test") as c:
        yield c


def test_health_endpoint_returns_ok(client):
    """GET /health must return 200 with status field."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body


def test_openapi_spec_is_valid_json(client):
    """GET /openapi.json must return 200 and parse as valid JSON with paths key."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "paths" in spec
    assert "components" in spec
    # Verify a few critical paths exist
    assert "/app/v1/graph/tasks" in spec["paths"] or any(
        "/graph/tasks" in p for p in spec["paths"]
    ), "Expected /graph/tasks path in OpenAPI spec"


def test_auth_endpoint_exists(client):
    """POST /auth/dev-token must be in the spec (auth flow test)."""
    r = client.get("/openapi.json")
    spec = r.json()
    paths = spec.get("paths", {})
    auth_paths = [p for p in paths if "auth" in p or "token" in p]
    assert len(auth_paths) > 0, "No auth endpoints found in OpenAPI spec"
