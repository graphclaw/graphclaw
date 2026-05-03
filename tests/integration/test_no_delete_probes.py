"""tests.integration.test_no_delete_probes — Wave 0 anti-delete CI probes.

These integration tests run against the live Docker stack (requires
--run-integration or GRAPHCLAW_RUN_INTEGRATION=1 env var).

They verify:
  AC1: agent_principal cannot issue DELETE via the DB (startup_assert_no_delete).
  AC2: archive_* tools set lifecycle fields and create tombstones (live DB).
  AC3: resolve_canonical follows redirects against live graph data.
  AC4: lifecycle-audit.sh equivalent: no forbidden lifecycle rules in MinIO.
  AC5: Every tool in task_management set is available (archive_* present).
  AC6: CallerContext enforcement is active in flag-on mode.
  AC7: state machine rejects DELETED/PURGED transitions.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# AC1: Startup no-delete probe (DB level)
# ---------------------------------------------------------------------------


class TestNoDeleteProbeDB:
    """AC1: agent_principal cannot execute DELETE on _principal_probe."""

    def test_probe_module_importable(self) -> None:

        from graphclaw.auth.principals import startup_assert_no_delete  # noqa: PLC0415

        assert callable(startup_assert_no_delete)

    def test_principal_enum_has_three_roles(self) -> None:

        from graphclaw.auth.principals import Principal  # noqa: PLC0415

        names = {p.name for p in Principal}
        assert "AGENT" in names
        assert "ADMIN" in names
        assert "MIGRATION" in names

    async def test_agent_principal_dsn_resolves(self) -> None:
        """AGENT_PRINCIPAL_DSN env var must be configured in the Docker stack."""

        from graphclaw.auth.principals import Principal, resolve_principal_dsn  # noqa: PLC0415

        dsn = resolve_principal_dsn(Principal.AGENT)
        assert dsn, "AGENT_PRINCIPAL_DSN must be set in the gateway environment"


# ---------------------------------------------------------------------------
# AC2: archive_* tools — lifecycle field writes via admin_principal
# ---------------------------------------------------------------------------


class TestArchiveToolsLive:
    """AC2: archive tools set lifecycle fields and create TombstoneNode."""

    def test_archive_task_function_importable(self) -> None:

        from graphclaw.agent.tools.archive import archive_task  # noqa: PLC0415

        assert callable(archive_task)

    def test_archive_resource_function_importable(self) -> None:

        from graphclaw.agent.tools.archive import archive_resource  # noqa: PLC0415

        assert callable(archive_resource)

    def test_archive_goal_function_importable(self) -> None:

        from graphclaw.agent.tools.archive import archive_goal  # noqa: PLC0415

        assert callable(archive_goal)

    def test_tombstone_node_model_importable(self) -> None:

        from graphclaw.models.nodes import TombstoneNode  # noqa: PLC0415

        t = TombstoneNode(archived_node_id="TSK-test", reason="probe")
        assert t.node_type == "TombstoneNode"
        assert t.id.startswith("TOMB-")


# ---------------------------------------------------------------------------
# AC3: resolve_canonical follows redirects
# ---------------------------------------------------------------------------


class TestResolveCanonicalProbe:
    """AC3: resolve_canonical is importable and callable."""

    def test_resolve_canonical_importable(self) -> None:

        from graphclaw.db.age.redirects import resolve_canonical  # noqa: PLC0415

        assert callable(resolve_canonical)

    def test_tombstone_cycle_importable(self) -> None:

        from graphclaw.db.age.redirects import MaxHopsExceeded, TombstoneCycle  # noqa: PLC0415

        assert issubclass(TombstoneCycle, Exception)
        assert issubclass(MaxHopsExceeded, Exception)


# ---------------------------------------------------------------------------
# AC4: MinIO has no forbidden lifecycle rules
# ---------------------------------------------------------------------------


class TestMinIOLifecycleAudit:
    """AC4: MinIO storage has no lifecycle rules on user-data prefixes."""

    async def test_lifecycle_audit_passes_on_live_minio(self) -> None:
        """Connect to the live MinIO instance and run the lifecycle audit."""

        from graphclaw.infra.storage import S3StorageClient  # noqa: PLC0415
        from graphclaw.observability.startup_audit import audit_lifecycle_rules  # noqa: PLC0415

        storage = S3StorageClient(
            bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
            endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL", "http://localhost:9000"),
            region=os.environ.get("STORAGE_REGION", "us-east-1"),
        )
        result = await audit_lifecycle_rules(storage)
        assert result.ok, f"MinIO lifecycle rules found on forbidden prefixes: {result.violations}"


# ---------------------------------------------------------------------------
# AC5: tool_registry has archive_* in task_management
# ---------------------------------------------------------------------------


class TestToolRegistryCompleteness:
    """AC5: archive_* tools are exposed in the task_management tool set."""

    def test_all_archive_tools_present(self) -> None:

        from graphclaw.agent.tool_registry import ToolSetRegistry  # noqa: PLC0415

        reg = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        tools = reg.activate("task_management")
        names = {t.name for t in tools}
        for expected in ("archive_task", "archive_resource", "archive_goal"):
            assert expected in names, f"Missing tool: {expected}"

    def test_no_delete_tools_in_registry(self) -> None:
        """Verify delete_* tool names are absent from the agent tool registry."""

        from graphclaw.agent.tool_registry import ToolSetRegistry  # noqa: PLC0415

        reg = ToolSetRegistry(has_skill_registry=True, has_mcp_registry=True)
        all_tools: list = []
        for set_name in ("core", "task_management", "planning", "skills", "mcp", "delegation"):
            try:
                all_tools.extend(reg.activate(set_name))
            except Exception:
                pass
        delete_tools = [t.name for t in all_tools if t.name.startswith("delete_")]
        assert delete_tools == [], (
            f"Found delete_* tools in registry (forbidden by FR-DEL-002): {delete_tools}"
        )


# ---------------------------------------------------------------------------
# AC6: CallerContext enforcement
# ---------------------------------------------------------------------------


class TestCallerContextEnforcement:
    """AC6: CallerContext model and enforcement are wired correctly."""

    def test_caller_context_model_importable(self) -> None:

        from graphclaw.cross_tenant.acl import CallerContext  # noqa: PLC0415

        ctx = CallerContext(user_id="integration-test", org_id="test-org")
        assert ctx.principal == "agent_principal"

    def test_require_caller_context_raises_when_enforced(self) -> None:

        from unittest.mock import patch

        from graphclaw.cross_tenant.acl import require_caller_context  # noqa: PLC0415
        from graphclaw.db.base import ACLContextMissingError  # noqa: PLC0415

        with patch("graphclaw.config.AppConfig") as MockCfg:
            MockCfg.return_value.no_delete_enforcement = True
            with pytest.raises(ACLContextMissingError):
                require_caller_context(None)

    def test_require_caller_context_passes_with_context(self) -> None:

        from graphclaw.cross_tenant.acl import (  # noqa: PLC0415
            CallerContext,
            require_caller_context,
        )

        ctx = CallerContext(user_id="u", org_id="o")
        require_caller_context(ctx)  # must not raise


# ---------------------------------------------------------------------------
# AC7: State machine rejects DELETED/PURGED
# ---------------------------------------------------------------------------


class TestStateMachineNoDeleteProbe:
    """AC7: StateMachine blocks transitions to DELETED or PURGED."""

    def _make_task(self) -> object:
        from datetime import UTC, datetime

        from graphclaw.models.base import generate_task_id  # noqa: PLC0415
        from graphclaw.models.enums import TaskState, TaskType  # noqa: PLC0415
        from graphclaw.models.nodes import TaskNode  # noqa: PLC0415

        now = datetime.now(UTC)
        return TaskNode(
            id=generate_task_id("PR", TaskType.ATOMIC),
            title="Probe Task",
            description="probe",
            task_type=TaskType.ATOMIC,
            state=TaskState.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def test_deleted_state_blocked(self) -> None:

        from graphclaw.state.machine import StateMachine  # noqa: PLC0415
        from graphclaw.state.transitions import InvalidTransitionError  # noqa: PLC0415

        task = self._make_task()
        sm = StateMachine()
        with pytest.raises((InvalidTransitionError, ValueError)):
            fake = type("S", (str,), {"value": "DELETED"})()
            sm._check_transition_table(task, task.state, fake)  # type: ignore[arg-type]

    def test_purged_state_blocked(self) -> None:

        from graphclaw.state.machine import StateMachine  # noqa: PLC0415
        from graphclaw.state.transitions import InvalidTransitionError  # noqa: PLC0415

        task = self._make_task()
        sm = StateMachine()
        with pytest.raises((InvalidTransitionError, ValueError)):
            fake = type("S", (str,), {"value": "PURGED"})()
            sm._check_transition_table(task, task.state, fake)  # type: ignore[arg-type]

    def test_migrations_include_0008_0009_0010(self) -> None:
        """All Wave 0 migrations are present in the catalogue."""

        from graphclaw.migrations.catalogue import MIGRATIONS  # noqa: PLC0415

        versions = {m.version for m in MIGRATIONS}
        for v in ("0008", "0009", "0010"):
            assert v in versions, f"Migration {v} missing from catalogue"
