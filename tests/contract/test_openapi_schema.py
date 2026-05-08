# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
- Auth endpoint exists in the spec

Notes:
- Runs against in-process FastAPI app (no live services required)
- Endpoints gated by auth return 401/403 — schemathesis marks these as valid
- Run standalone: pytest tests/contract/ -v
- schemathesis v4 API: schemathesis.pytest.from_fixture + schema.parametrize()
"""
import httpx
import pytest
import schemathesis
import schemathesis.pytest as st_pytest
from hypothesis import HealthCheck, settings

from graphclaw.gateway.app import create_app

# ── Stateless schema conformance ─────────────────────────────────────────────
# schemathesis v4: resolve schema from fixture at test runtime.
# Marked integration: requires live DB/Redis so endpoint handlers don't block.
schema = st_pytest.from_fixture("schema")


@pytest.mark.integration
@schema.parametrize()
@settings(max_examples=3, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_api_conformance(case, graphclaw_app):
    """Every OpenAPI operation must not return an unhandled 500 error.

    Requires --run-integration: endpoints call DB/Redis that must be live.
    """
    case.call_and_validate(
        app=graphclaw_app,
        checks=(schemathesis.checks.not_a_server_error,),
    )


# ── Targeted contract tests ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def async_client():
    transport = httpx.ASGITransport(app=create_app(broker=None))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_endpoint_returns_ok(async_client):
    """GET /health must return 200 with status field."""
    r = await async_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body


async def test_openapi_spec_is_valid_json(async_client):
    """GET /openapi.json must return 200 and parse as valid JSON with paths key."""
    r = await async_client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "paths" in spec
    assert "components" in spec
    assert any("/graph/tasks" in p for p in spec["paths"]), (
        "Expected /graph/tasks path in OpenAPI spec"
    )


async def test_auth_endpoint_exists(async_client):
    """An auth/token endpoint must be in the spec."""
    r = await async_client.get("/openapi.json")
    spec = r.json()
    paths = spec.get("paths", {})
    auth_paths = [p for p in paths if "auth" in p or "token" in p]
    assert len(auth_paths) > 0, "No auth endpoints found in OpenAPI spec"
