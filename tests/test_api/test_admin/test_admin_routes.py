"""tests.test_api.test_admin.test_admin_routes — Admin panel endpoint tests.

Covers all 9 admin modules:
- members, features, llm, judge, guardrails, sso, audit, infra, connectors
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_api.test_admin.conftest import make_admin_app

_ADMIN_USER = "USER-admin-test-001"


# ============================================================================
# Members
# ============================================================================


def test_list_members_no_org_returns_empty() -> None:
    """GET /admin/members returns [] when no org exists."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/members")
    assert response.status_code == 200
    assert response.json() == []


def test_list_members_requires_admin_role() -> None:
    """GET /admin/members returns 403 for non-admin role."""
    app, _, _, _ = make_admin_app(role="USER")
    client = TestClient(app)
    response = client.get("/app/v1/admin/members")
    assert response.status_code == 403


def test_invite_member_no_org_returns_404() -> None:
    """POST /admin/members/invite returns 404 when no org exists."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/admin/members/invite",
        json={"email": "alice@example.com", "role": "MEMBER"},
    )
    assert response.status_code == 404


def _seed_org(graph_store, owner_id: str = _ADMIN_USER) -> str:
    org_id = "ORG-admintest001"
    from graphclaw.models.base import utcnow

    graph_store._nodes[org_id] = {
        "id": org_id,
        "name": "Test Org",
        "owner_id": owner_id,
        "members": [],
        "domain": None,
        "created_at": utcnow().isoformat(),
        "updated_at": utcnow().isoformat(),
        "version": 0,
    }
    return org_id


def test_invite_member_adds_to_org() -> None:
    """POST /admin/members/invite adds a member record to the org."""
    app, _, graph, _ = make_admin_app()
    _seed_org(graph)
    client = TestClient(app)
    response = client.post(
        "/app/v1/admin/members/invite",
        json={"email": "bob@example.com", "role": "MEMBER"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "bob@example.com"
    assert data["role"] == "MEMBER"


def test_patch_member_not_found_returns_404() -> None:
    """PATCH /admin/members/{id} returns 404 for unknown member."""
    app, _, graph, _ = make_admin_app()
    _seed_org(graph)
    client = TestClient(app)
    response = client.patch("/app/v1/admin/members/USER-ghost", json={"role": "ADMIN"})
    assert response.status_code == 404


def test_remove_member_not_found_returns_404() -> None:
    """DELETE /admin/members/{id} returns 404 for unknown member."""
    app, _, graph, _ = make_admin_app()
    _seed_org(graph)
    client = TestClient(app)
    response = client.delete("/app/v1/admin/members/USER-ghost")
    assert response.status_code == 404


# ============================================================================
# Features
# ============================================================================


def test_get_features_returns_defaults() -> None:
    """GET /admin/features returns default feature policy."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/features")
    assert response.status_code == 200
    data = response.json()
    assert "enable_agent_canvas" in data


def test_put_features_persists() -> None:
    """PUT /admin/features persists the policy."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    body = {
        "enable_agent_canvas": False,
        "enable_mcp_integration": True,
        "enable_skill_marketplace": True,
        "enable_multi_channel": False,
        "enable_a2a": True,
        "extra": {},
    }
    response = client.put("/app/v1/admin/features", json=body)
    assert response.status_code == 200
    assert response.json()["enable_agent_canvas"] is False


def test_get_channel_policy_returns_defaults() -> None:
    """GET /admin/features/channels returns default channel policy."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/features/channels")
    assert response.status_code == 200
    assert "allowed_channels" in response.json()


def test_get_mcp_allowlist_returns_defaults() -> None:
    """GET /admin/features/mcp-allowlist returns default MCP allowlist."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/features/mcp-allowlist")
    assert response.status_code == 200
    assert "allowed_servers" in response.json()


def test_get_marketplace_policy_returns_defaults() -> None:
    """GET /admin/features/marketplace returns default marketplace policy."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/features/marketplace")
    assert response.status_code == 200
    assert "enabled" in response.json()


# ============================================================================
# LLM
# ============================================================================


def test_get_providers_returns_defaults() -> None:
    """GET /admin/llm/providers returns empty providers list by default."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/llm/providers")
    assert response.status_code == 200
    assert "providers" in response.json()


def test_store_llm_key() -> None:
    """POST /admin/llm/keys stores an org-level LLM key."""
    app, _, _, secrets = make_admin_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/admin/llm/keys",
        json={"provider": "anthropic", "api_key": "sk-org-test"},
    )
    assert response.status_code == 200
    assert response.json()["stored"] is True
    assert "graphclaw/org/llm/anthropic" in secrets._store


def test_delete_llm_key_returns_204() -> None:
    """DELETE /admin/llm/keys/{provider} returns 204 after deletion."""
    app, _, _, secrets = make_admin_app()
    client = TestClient(app)
    client.post("/app/v1/admin/llm/keys", json={"provider": "openai", "api_key": "sk-openai"})
    response = client.delete("/app/v1/admin/llm/keys/openai")
    assert response.status_code == 204


def test_delete_llm_key_not_found_returns_404() -> None:
    """DELETE /admin/llm/keys/{provider} returns 404 for missing key."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.delete("/app/v1/admin/llm/keys/unknown-provider")
    assert response.status_code == 404


def test_get_budget_returns_defaults() -> None:
    """GET /admin/llm/budget returns default budget config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/llm/budget")
    assert response.status_code == 200
    assert "daily_limit_usd" in response.json()


def test_put_budget_persists() -> None:
    """PUT /admin/llm/budget persists the budget config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    body = {
        "daily_limit_usd": 50.0,
        "monthly_limit_usd": 1000.0,
        "alert_threshold_pct": 0.9,
        "cost_anomaly_sigma": 2.5,
    }
    response = client.put("/app/v1/admin/llm/budget", json=body)
    assert response.status_code == 200
    assert response.json()["daily_limit_usd"] == 50.0


# ============================================================================
# Judge
# ============================================================================


def test_get_judge_config_returns_defaults() -> None:
    """GET /admin/llm-judge/config returns default judge config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/llm-judge/config")
    assert response.status_code == 200
    assert "enabled" in response.json()


def test_put_judge_config_persists() -> None:
    """PUT /admin/llm-judge/config persists judge config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    body = {
        "enabled": True,
        "judge_model": "claude-haiku-4-5-20251001",
        "sample_rate": 0.2,
        "criteria": ["accuracy"],
        "auto_flag_threshold": 0.5,
        "extra": {},
    }
    response = client.put("/app/v1/admin/llm-judge/config", json=body)
    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_list_judge_results_returns_list() -> None:
    """GET /admin/llm-judge/results returns a list."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/llm-judge/results")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_judge_stats_returns_defaults() -> None:
    """GET /admin/llm-judge/stats returns zero stats for empty history."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/llm-judge/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_evaluations"] == 0


# ============================================================================
# Guardrails
# ============================================================================


def test_get_guardrails_returns_empty_rules() -> None:
    """GET /admin/guardrails returns empty rules by default."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/guardrails")
    assert response.status_code == 200
    assert response.json()["rules"] == []


def test_validate_guardrails_valid() -> None:
    """POST /admin/guardrails/validate returns valid=True for a clean rule set."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    body = {
        "version": "1.0",
        "rules": [
            {
                "rule_id": "R1",
                "name": "No swearing",
                "pattern": "badword",
                "action": "BLOCK",
                "enabled": True,
                "description": "",
            }
        ],
    }
    response = client.post("/app/v1/admin/guardrails/validate", json=body)
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validate_guardrails_duplicate_id() -> None:
    """POST /admin/guardrails/validate returns valid=False for duplicate rule IDs."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    rule = {
        "rule_id": "R1",
        "name": "Rule",
        "pattern": "test",
        "action": "BLOCK",
        "enabled": True,
        "description": "",
    }
    body = {"version": "1.0", "rules": [rule, rule]}
    response = client.post("/app/v1/admin/guardrails/validate", json=body)
    data = response.json()
    assert data["valid"] is False
    assert len(data["errors"]) > 0


def test_test_guardrails_no_match() -> None:
    """POST /admin/guardrails/test returns blocked=False for safe message."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/admin/guardrails/test",
        json={"message": "Hello, world!"},
    )
    assert response.status_code == 200
    assert response.json()["blocked"] is False


def test_guardrails_metrics_returns_defaults() -> None:
    """GET /admin/guardrails/metrics returns zero metrics by default."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/guardrails/metrics")
    assert response.status_code == 200
    assert response.json()["total_requests"] == 0


# ============================================================================
# SSO
# ============================================================================


def test_get_sso_returns_defaults() -> None:
    """GET /admin/sso returns default SSO config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/sso")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_put_sso_persists() -> None:
    """PUT /admin/sso persists the SSO config."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    body = {
        "provider": "google",
        "enabled": True,
        "enforced": False,
        "client_id": "cid",
        "issuer_url": "https://accounts.google.com",
        "metadata_url": "",
        "allowed_domains": ["example.com"],
        "extra": {},
    }
    response = client.put("/app/v1/admin/sso", json=body)
    assert response.status_code == 200
    assert response.json()["provider"] == "google"


def test_sso_test_returns_response() -> None:
    """POST /admin/sso/test returns a reachable/error response."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post("/app/v1/admin/sso/test")
    assert response.status_code == 200
    data = response.json()
    assert "reachable" in data


def test_sso_enforce_toggle() -> None:
    """PATCH /admin/sso/enforce updates the enforced flag."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.patch("/app/v1/admin/sso/enforce", json={"enforced": True})
    assert response.status_code == 200
    assert response.json()["enforced"] is True


# ============================================================================
# Audit log
# ============================================================================


def test_query_audit_log_returns_empty() -> None:
    """GET /admin/audit-log returns [] when no entries exist."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/audit-log")
    assert response.status_code == 200
    assert response.json() == []


def test_query_audit_log_with_stored_entries() -> None:
    """GET /admin/audit-log returns stored entries."""
    app, storage, _, _ = make_admin_app()
    entries = [
        {
            "event_id": "EVT-001",
            "actor_id": _ADMIN_USER,
            "action": "LOGIN",
            "resource_type": "auth",
            "resource_id": "",
            "timestamp": "2026-04-10T10:00:00Z",
        }
    ]
    storage._data["admin/audit/log.json"] = json.dumps(entries).encode()
    client = TestClient(app)
    response = client.get("/app/v1/admin/audit-log")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["event_id"] == "EVT-001"


# ============================================================================
# Infra
# ============================================================================


def test_deployment_status_returns_response() -> None:
    """GET /admin/deployment/status returns a deployment status."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/deployment/status")
    assert response.status_code == 200
    assert "overall" in response.json()


def test_cluster_health_returns_response() -> None:
    """GET /admin/cluster/health returns a cluster health response."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/cluster/health")
    assert response.status_code == 200
    assert "status" in response.json()


def test_list_backups_returns_list() -> None:
    """GET /admin/backups returns a list."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/backups")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_security_status_returns_response() -> None:
    """GET /admin/security/status returns a security posture summary."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/security/status")
    assert response.status_code == 200
    assert response.json()["overall"] == "ok"


def test_list_alarms_returns_empty() -> None:
    """GET /admin/alarms returns [] when no alarms are stored."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/alarms")
    assert response.status_code == 200
    assert response.json() == []


def test_patch_alarm_not_found_returns_404() -> None:
    """PATCH /admin/alarms/{id} returns 404 for unknown alarm."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.patch("/app/v1/admin/alarms/ALM-ghost", json={"acknowledged": True})
    assert response.status_code == 404


def test_list_migrations_returns_list() -> None:
    """GET /admin/migrations returns a list."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/migrations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_apply_migrations_dry_run() -> None:
    """POST /admin/migrations/apply with dry_run=true returns preview."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post("/app/v1/admin/migrations/apply", json={"dry_run": True})
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    assert data["applied"] == []


# ============================================================================
# Connectors
# ============================================================================


def test_list_connectors_empty_returns_list() -> None:
    """GET /admin/connectors returns [] when no connectors configured."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/connectors")
    assert response.status_code == 200
    assert response.json() == []


def test_create_connector_returns_201() -> None:
    """POST /admin/connectors creates a connector and returns 201."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/admin/connectors",
        json={"name": "Jira Prod", "type": "jira", "config": {}},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Jira Prod"
    assert data["connector_id"].startswith("CONN-")


def test_sync_connector_not_found_returns_404() -> None:
    """POST /admin/connectors/{id}/sync returns 404 for unknown connector."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.post("/app/v1/admin/connectors/CONN-ghost/sync")
    assert response.status_code == 404


def test_sync_connector_returns_202() -> None:
    """POST /admin/connectors/{id}/sync returns 202 for existing connector."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    created = client.post(
        "/app/v1/admin/connectors",
        json={"name": "Notion", "type": "notion", "config": {}},
    ).json()
    response = client.post(f"/app/v1/admin/connectors/{created['connector_id']}/sync")
    assert response.status_code == 202
    assert response.json()["status"] == "triggered"


def test_connector_health_not_found_returns_404() -> None:
    """GET /admin/connectors/{id}/health returns 404 for unknown connector."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    response = client.get("/app/v1/admin/connectors/CONN-ghost/health")
    assert response.status_code == 404


def test_connector_health_returns_response() -> None:
    """GET /admin/connectors/{id}/health returns a health response."""
    app, _, _, _ = make_admin_app()
    client = TestClient(app)
    created = client.post(
        "/app/v1/admin/connectors",
        json={"name": "Asana", "type": "asana", "config": {}},
    ).json()
    response = client.get(f"/app/v1/admin/connectors/{created['connector_id']}/health")
    assert response.status_code == 200
    assert "reachable" in response.json()
