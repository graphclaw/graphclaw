"""tests.test_cli.test_intelligence_agents_commands — Tests for
``graphclaw intelligence agents`` CLI sub-commands (list, delete, audit).

Uses Typer's CliRunner and patches ``_storage_client`` in the intelligence
commands module so no real MinIO connection is needed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from graphclaw.cli.main import app
from graphclaw.infra.storage import StoragePaths
from tests.test_api.conftest import FakeStorageClient

runner = CliRunner()

_USER = "USR-cli-test-001"


def _make_client(*agent_ids: str, with_canvas: bool = False) -> FakeStorageClient:
    """Return a FakeStorageClient pre-populated with one or more agents."""
    client = FakeStorageClient()
    for agent_id in agent_ids:
        profile = StoragePaths.agent_profile(_USER, agent_id)
        manifest = StoragePaths.agent_manifest(_USER, agent_id)
        working = StoragePaths.agent_memory_working(_USER, agent_id)

        client._data[profile] = f"# {agent_id}\n".encode()
        client._data[manifest] = json.dumps({"name": agent_id.replace("-", " ").title()}).encode()
        client._data[working] = b"some context\n"

    if with_canvas:
        for agent_id in agent_ids:
            canvas_key = f"agents/{_USER}/definitions/{agent_id}.json"
            client._data[canvas_key] = json.dumps({"agent_id": agent_id}).encode()

    return client


def _run(*args: str, client: FakeStorageClient | None = None, user: str = _USER):
    env = {"GRAPHCLAW_USER_ID": user}
    storage = client or FakeStorageClient()
    with patch(
        "graphclaw.cli.intelligence_commands._storage_client",
        return_value=storage,
    ):
        return runner.invoke(
            app, ["intelligence", "agents", *args], env=env, catch_exceptions=False
        )


# ---------------------------------------------------------------------------
# agents list
# ---------------------------------------------------------------------------


def test_agents_list_empty() -> None:
    """agents list prints a 'No sub-agents found' message when storage is empty."""
    result = _run("list")
    assert result.exit_code == 0
    assert "No sub-agents" in result.output


def test_agents_list_shows_agent_ids() -> None:
    """agents list shows agent IDs in a table."""
    client = _make_client("research-agent", "comms-helper")
    result = _run("list", client=client)
    assert result.exit_code == 0
    assert "research-agent" in result.output
    assert "comms-helper" in result.output


def test_agents_list_shows_name_from_manifest() -> None:
    """agents list reads the name field from manifest.json."""
    client = _make_client("my-agent")
    result = _run("list", client=client)
    assert result.exit_code == 0
    assert "My Agent" in result.output


def test_agents_list_marks_profile_present() -> None:
    """agents list marks the Profile column ✓ when profile.md exists."""
    client = _make_client("has-profile")
    result = _run("list", client=client)
    assert result.exit_code == 0
    assert "has-profile" in result.output


def test_agents_list_requires_user_id() -> None:
    """agents list exits 1 when GRAPHCLAW_USER_ID is blank."""
    with patch(
        "graphclaw.cli.intelligence_commands._storage_client",
        return_value=FakeStorageClient(),
    ):
        result = runner.invoke(
            app,
            ["intelligence", "agents", "list"],
            env={"GRAPHCLAW_USER_ID": ""},
            catch_exceptions=False,
        )
    assert result.exit_code == 1
    assert "GRAPHCLAW_USER_ID" in result.output


# ---------------------------------------------------------------------------
# agents delete
# ---------------------------------------------------------------------------


def test_agents_delete_removes_all_objects() -> None:
    """agents delete --yes removes profile, manifest, and memory objects."""
    client = _make_client("to-delete")
    prefix = StoragePaths.agents_prefix(_USER)
    assert any(k.startswith(prefix) for k in client._data)

    result = _run("delete", "to-delete", "--yes", client=client)
    assert result.exit_code == 0
    assert "Deleted" in result.output
    remaining = [k for k in client._data if "to-delete" in k and k.startswith(prefix)]
    assert remaining == []


def test_agents_delete_reports_count() -> None:
    """agents delete --yes reports how many objects were removed."""
    client = _make_client("counted-agent")
    result = _run("delete", "counted-agent", "--yes", client=client)
    assert result.exit_code == 0
    assert "3" in result.output  # profile + manifest + working = 3 objects


def test_agents_delete_missing_agent_exits_1() -> None:
    """agents delete --yes exits 1 when no objects exist for that agent."""
    result = _run("delete", "ghost-agent", "--yes")
    assert result.exit_code == 1


def test_agents_delete_leaves_other_agents_intact() -> None:
    """agents delete --yes only removes objects for the named agent."""
    client = _make_client("alpha", "beta")
    _run("delete", "alpha", "--yes", client=client)
    beta_profile = StoragePaths.agent_profile(_USER, "beta")
    assert beta_profile in client._data


# ---------------------------------------------------------------------------
# agents audit
# ---------------------------------------------------------------------------


def test_agents_audit_no_agents() -> None:
    """agents audit prints the table even when no agents exist."""
    result = _run("audit")
    assert result.exit_code == 0
    assert "No orphaned" in result.output


def test_agents_audit_runtime_only_flagged_as_orphan() -> None:
    """agents audit marks runtime-only agents as 'runtime-only (orphan?)'."""
    client = _make_client("orphan-agent")  # no canvas definition
    result = _run("audit", client=client)
    assert result.exit_code == 0
    assert "orphan-agent" in result.output
    assert "runtime-only" in result.output


def test_agents_audit_both_shows_ok() -> None:
    """agents audit marks agents with both runtime and canvas definition as OK."""
    client = _make_client("linked-agent", with_canvas=True)
    result = _run("audit", client=client)
    assert result.exit_code == 0
    assert "linked-agent" in result.output
    assert "OK" in result.output


def test_agents_audit_canvas_only_flagged() -> None:
    """agents audit marks canvas-only agents as 'canvas-only (not provisioned)'."""
    client = FakeStorageClient()
    # Only canvas definition, no runtime files
    client._data[f"agents/{_USER}/definitions/canvas-only.json"] = b"{}"
    result = _run("audit", client=client)
    assert result.exit_code == 0
    assert "canvas-only" in result.output
    assert "canvas-only (not provisioned)" in result.output


def test_agents_audit_mixed_scenario() -> None:
    """agents audit correctly categorises all three states simultaneously."""
    client = _make_client("runtime-only", "both-present")
    client._data[f"agents/{_USER}/definitions/both-present.json"] = b"{}"
    client._data[f"agents/{_USER}/definitions/canvas-def.json"] = b"{}"

    result = _run("audit", client=client)
    assert result.exit_code == 0
    assert "runtime-only" in result.output
    assert "both-present" in result.output
    assert "canvas-def" in result.output
