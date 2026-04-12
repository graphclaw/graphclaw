"""tests.test_api.test_intelligence_routes — Tests for Intelligence Hub endpoints.

Description
-----------
Covers all /app/v1/intelligence/* endpoints using in-memory FakeStorageClient
and auth dependency override so no real S3/MinIO or JWT stack is needed.

Test groups
-----------
- Agent profile: GET/PUT profile.md
- Working context: GET/PUT working context, POST compact
- Episodic memory: list, get, delete
- Semantic memory: list, get, put, delete
- Skill authoring: list, create, get, update, delete, fork
- Skill validate: valid and invalid SKILL.md content
- Skill import: multipart file upload

Design Patterns
---------------
- ``app.dependency_overrides``: Auth and storage replaced with deterministic fakes.
- ``FakeStorageClient`` from conftest: In-memory bytes store.
- ``TestClient``: Synchronous ASGI test client.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- tests.test_api.conftest: FakeStorageClient.
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "usr-intelligence-test-001"
_AGENT_ID = "main"

_SAMPLE_SKILL_MD = """\
---
name: test-authored-skill
description: A test authored skill
version: 1.0.0
model: claude-haiku-4-5-20251001
tags:
  - test
---
You are a test assistant.
"""

_INVALID_SKILL_MD = "This is not a valid SKILL.md — no frontmatter at all."


def _make_app(storage: FakeStorageClient) -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_storage() -> FakeStorageClient:
        return storage

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage
    return app


@pytest.fixture()
def storage() -> FakeStorageClient:
    return FakeStorageClient()


@pytest.fixture()
def client(storage: FakeStorageClient) -> TestClient:
    return TestClient(_make_app(storage))


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------


def test_get_profile_returns_default_when_absent(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"] == _AGENT_ID
    assert "no profile defined" in body["content"].lower() or "agent" in body["content"].lower()


def test_put_profile_then_get(client: TestClient) -> None:
    content = "# Main Agent\n\nPersona: helpful and concise.\n"
    r = client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/profile",
        json={"content": content},
    )
    assert r.status_code == 200

    r2 = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/profile")
    assert r2.status_code == 200
    assert r2.json()["content"] == content


# ---------------------------------------------------------------------------
# Working context
# ---------------------------------------------------------------------------


def test_get_working_returns_empty_when_absent(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working")
    assert r.status_code == 200
    body = r.json()
    assert body["memory_type"] == "working"
    assert body["content"] == ""


def test_put_working_then_get(client: TestClient) -> None:
    ctx = "Currently working on Task-001: refactor auth module."
    r = client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working",
        json={"content": ctx},
    )
    assert r.status_code == 200

    r2 = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working")
    assert r2.json()["content"] == ctx


# ---------------------------------------------------------------------------
# Compact
# ---------------------------------------------------------------------------


def test_compact_replaces_working_context(client: TestClient) -> None:
    ctx = "Long detailed context that needs compacting..."
    client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working",
        json={"content": ctx},
    )

    r = client.post(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/compact",
        json={"summary": "Compact summary: auth refactor done.", "session_label": "test-ses"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["working_context_replaced"] is True
    assert "test-ses" in body["archived_as"]


def test_compact_working_is_replaced_with_summary(client: TestClient, storage: FakeStorageClient) -> None:
    ctx = "Old working context"
    client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working",
        json={"content": ctx},
    )
    summary = "New compact summary"
    client.post(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/compact",
        json={"summary": summary},
    )

    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working")
    assert r.json()["content"] == summary


def test_compact_archives_to_episodic(client: TestClient) -> None:
    ctx = "Context to archive"
    client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working",
        json={"content": ctx},
    )
    r = client.post(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/compact",
        json={"summary": "Summary"},
    )
    archived_as = r.json()["archived_as"]

    # The archived entry should now appear in episodic list
    r2 = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/episodic")
    keys = [e["key"] for e in r2.json()["entries"]]
    assert archived_as in keys


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------


def test_episodic_list_empty(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/episodic")
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_episodic_get_missing_returns_404(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/episodic/nonexistent.md")
    assert r.status_code == 404


def test_episodic_delete(client: TestClient, storage: FakeStorageClient) -> None:
    from graphclaw.infra.storage import StoragePaths

    entry = "2026-04-11-session.md"
    path = StoragePaths.agent_memory_episodic_entry(_TEST_USER, _AGENT_ID, entry)
    storage._data[path] = b"# Session\n\nSome content."

    r = client.delete(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/episodic/{entry}")
    assert r.status_code == 204
    assert path not in storage._data


# ---------------------------------------------------------------------------
# Semantic memory
# ---------------------------------------------------------------------------


def test_semantic_list_empty(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic")
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_semantic_put_then_get(client: TestClient) -> None:
    r = client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/users",
        json={"content": "# Users\n\nAlice prefers short responses.\n"},
    )
    assert r.status_code == 200

    r2 = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/users")
    assert r2.status_code == 200
    assert "Alice" in r2.json()["content"]


def test_semantic_get_missing_returns_404(client: TestClient) -> None:
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/unknown-topic")
    assert r.status_code == 404


def test_semantic_delete(client: TestClient, storage: FakeStorageClient) -> None:
    from graphclaw.infra.storage import StoragePaths

    path = StoragePaths.agent_memory_semantic_topic(_TEST_USER, _AGENT_ID, "projects")
    storage._data[path] = b"# Projects\n\nAlpha project context."

    r = client.delete(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/projects")
    assert r.status_code == 204
    assert path not in storage._data


def test_semantic_list_shows_written_topic(client: TestClient) -> None:
    client.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/patterns",
        json={"content": "# Patterns\n\nRecurring observations."},
    )
    r = client.get(f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic")
    keys = [e["key"] for e in r.json()["entries"]]
    assert "patterns" in keys


# ---------------------------------------------------------------------------
# Skill authoring
# ---------------------------------------------------------------------------


def test_authored_skills_list_empty(client: TestClient) -> None:
    r = client.get("/app/v1/intelligence/skills/authored")
    assert r.status_code == 200
    assert r.json() == []


def test_create_authored_skill(client: TestClient) -> None:
    r = client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "my-test-skill", "content": _SAMPLE_SKILL_MD},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["skill_id"] == "my-test-skill"
    assert body["content"] == _SAMPLE_SKILL_MD


def test_create_authored_skill_autogenerates_id_when_absent(client: TestClient) -> None:
    r = client.post(
        "/app/v1/intelligence/skills/authored",
        json={"content": _SAMPLE_SKILL_MD},
    )
    assert r.status_code == 201
    assert r.json()["skill_id"].startswith("authored-")


def test_create_authored_skill_conflict(client: TestClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "conflict-skill", "content": _SAMPLE_SKILL_MD},
    )
    r2 = client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "conflict-skill", "content": _SAMPLE_SKILL_MD},
    )
    assert r2.status_code == 409


def test_get_authored_skill(client: TestClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "fetch-skill", "content": _SAMPLE_SKILL_MD},
    )
    r = client.get("/app/v1/intelligence/skills/authored/fetch-skill")
    assert r.status_code == 200
    assert r.json()["content"] == _SAMPLE_SKILL_MD


def test_get_authored_skill_missing_returns_404(client: TestClient) -> None:
    r = client.get("/app/v1/intelligence/skills/authored/nonexistent")
    assert r.status_code == 404


def test_update_authored_skill(client: TestClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "update-skill", "content": _SAMPLE_SKILL_MD},
    )
    new_content = _SAMPLE_SKILL_MD.replace("A test authored skill", "Updated description")
    r = client.put(
        "/app/v1/intelligence/skills/authored/update-skill",
        json={"content": new_content},
    )
    assert r.status_code == 200
    assert "Updated description" in r.json()["content"]


def test_delete_authored_skill(client: TestClient, storage: FakeStorageClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "delete-skill", "content": _SAMPLE_SKILL_MD},
    )
    r = client.delete("/app/v1/intelligence/skills/authored/delete-skill")
    assert r.status_code == 204

    r2 = client.get("/app/v1/intelligence/skills/authored/delete-skill")
    assert r2.status_code == 404


def test_fork_authored_skill(client: TestClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "fork-source", "content": _SAMPLE_SKILL_MD},
    )
    r = client.post("/app/v1/intelligence/skills/authored/fork-source/fork")
    assert r.status_code == 201
    body = r.json()
    assert body["original_skill_id"] == "fork-source"
    assert "fork-source-fork-" in body["forked_skill_id"]


def test_fork_missing_skill_returns_404(client: TestClient) -> None:
    r = client.post("/app/v1/intelligence/skills/authored/ghost-skill/fork")
    assert r.status_code == 404


def test_list_authored_skills_shows_created(client: TestClient) -> None:
    client.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "listed-skill", "content": _SAMPLE_SKILL_MD},
    )
    r = client.get("/app/v1/intelligence/skills/authored")
    ids = [e["skill_id"] for e in r.json()]
    assert "listed-skill" in ids


# ---------------------------------------------------------------------------
# Skill validate
# ---------------------------------------------------------------------------


def test_validate_valid_skill_md(client: TestClient) -> None:
    r = client.post(
        "/app/v1/intelligence/skills/validate",
        json={"content": _SAMPLE_SKILL_MD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["parsed"]["name"] == "test-authored-skill"
    assert body["errors"] == []


def test_validate_invalid_skill_md(client: TestClient) -> None:
    r = client.post(
        "/app/v1/intelligence/skills/validate",
        json={"content": _INVALID_SKILL_MD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert len(body["errors"]) > 0


# ---------------------------------------------------------------------------
# Skill import
# ---------------------------------------------------------------------------


def test_import_valid_skill_file(client: TestClient) -> None:
    file_bytes = _SAMPLE_SKILL_MD.encode()
    r = client.post(
        "/app/v1/intelligence/skills/import",
        files={"file": ("SKILL.md", io.BytesIO(file_bytes), "text/markdown")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["skill_id"] == "test-authored-skill"


def test_import_invalid_skill_file_returns_422(client: TestClient) -> None:
    file_bytes = _INVALID_SKILL_MD.encode()
    r = client.post(
        "/app/v1/intelligence/skills/import",
        files={"file": ("SKILL.md", io.BytesIO(file_bytes), "text/markdown")},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Multi-tenant isolation check
# ---------------------------------------------------------------------------


def test_intelligence_paths_are_scoped_to_user(storage: FakeStorageClient) -> None:
    """All writes from intelligence endpoints must go under the test user's prefix."""
    app = _make_app(storage)
    c = TestClient(app)

    c.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/profile",
        json={"content": "# Profile"},
    )
    c.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/working",
        json={"content": "Working ctx"},
    )
    c.put(
        f"/app/v1/intelligence/agents/{_AGENT_ID}/memory/semantic/users",
        json={"content": "# Users"},
    )
    c.post(
        "/app/v1/intelligence/skills/authored",
        json={"skill_id": "isolation-skill", "content": _SAMPLE_SKILL_MD},
    )

    for path in storage._data:
        assert path.startswith(_TEST_USER + "/"), (
            f"Found a path not under user prefix: {path!r}"
        )
