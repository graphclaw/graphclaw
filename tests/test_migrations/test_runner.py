"""tests.test_migrations.test_runner — Unit tests for migrations runner and catalogue.

Description
-----------
Tests the ``MigrationRunner``, ``Migration``, and ``MIGRATIONS`` catalogue using
``unittest.mock.AsyncMock`` to simulate the database connection.  No live Postgres
instance is required.

Test Coverage
-------------
- Catalogue ordering and uniqueness invariants.
- Migration immutability (frozen dataclass).
- Runner apply logic: destructive guard, skip already-applied, stop on failure.
- ECS deployment config helpers: circuit breaker dict, AppSpec YAML, config coverage.
"""

# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.migrations.catalogue import MIGRATIONS
from graphclaw.migrations.models import Migration, MigrationError, MigrationStatus
from graphclaw.migrations.runner import MigrationRunner
from infra.deployment.ecs_deploy import (
    build_deployment_circuit_breaker,
    build_ecs_service_config,
    generate_appspec_yaml,
)
from infra.deployment.models import DEPLOYMENT_CONFIGS, DeploymentConfig, RolloutStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner() -> MigrationRunner:
    """Return a MigrationRunner with a fake DSN."""
    return MigrationRunner("postgresql://test:test@localhost:5432/test")


def _simple_migration(version: str = "0001", is_destructive: bool = False) -> Migration:
    return Migration(
        version=version,
        name="test_migration",
        description="A test migration",
        sql_up="SELECT 1;",
        is_destructive=is_destructive,
    )


# ---------------------------------------------------------------------------
# Catalogue invariant tests (no DB needed)
# ---------------------------------------------------------------------------


class TestCatalogueInvariants:
    """Static checks on the MIGRATIONS catalogue list."""

    def test_migration_version_ordering(self) -> None:
        """MIGRATIONS must be in ascending version order."""
        versions = [m.version for m in MIGRATIONS]
        assert versions == sorted(versions), (
            f"MIGRATIONS are not in ascending version order: {versions}"
        )

    def test_no_destructive_migrations(self) -> None:
        """No migration in the catalogue may be marked is_destructive=True."""
        destructive = [m for m in MIGRATIONS if m.is_destructive]
        assert not destructive, (
            f"Found destructive migrations: {[m.version for m in destructive]}"
        )

    def test_catalogue_versions_unique(self) -> None:
        """All version strings in MIGRATIONS must be unique."""
        versions = [m.version for m in MIGRATIONS]
        assert len(versions) == len(set(versions)), (
            f"Duplicate versions found: {versions}"
        )

    def test_catalogue_versions_sequential(self) -> None:
        """Versions must be sequential with no gaps: '0001', '0002', ..."""
        versions = [m.version for m in MIGRATIONS]
        for i, version in enumerate(versions, start=1):
            expected = str(i).zfill(4)
            assert version == expected, (
                f"Expected version {expected!r} at index {i - 1}, got {version!r}"
            )


# ---------------------------------------------------------------------------
# Migration model tests
# ---------------------------------------------------------------------------


class TestMigrationModel:
    """Tests for the Migration frozen dataclass."""

    def test_migration_frozen(self) -> None:
        """Migration instances must be immutable (frozen=True)."""
        m = _simple_migration()
        with pytest.raises((FrozenInstanceError, TypeError)):
            m.version = "9999"  # type: ignore[misc]

    def test_migration_default_not_destructive(self) -> None:
        """is_destructive defaults to False."""
        m = _simple_migration()
        assert m.is_destructive is False

    def test_migration_applied_at_defaults_none(self) -> None:
        """applied_at defaults to None in catalogue definitions."""
        m = _simple_migration()
        assert m.applied_at is None


# ---------------------------------------------------------------------------
# MigrationRunner tests (all DB calls mocked)
# ---------------------------------------------------------------------------


class TestMigrationRunner:
    """Tests for MigrationRunner.apply and apply_all logic."""

    def _patch_runner(
        self,
        runner: MigrationRunner,
        applied_versions: set[str] | None = None,
        execute_side_effect: Exception | None = None,
    ) -> tuple[AsyncMock, AsyncMock]:
        """Patch get_applied_versions and the psycopg connection on the runner.

        Returns (mock_conn, mock_get_applied_versions).
        """
        if applied_versions is None:
            applied_versions = set()

        # Patch get_applied_versions
        mock_get_applied = AsyncMock(return_value=applied_versions)

        # Build a mock psycopg async connection
        mock_conn = AsyncMock()

        if execute_side_effect is not None:
            mock_conn.execute = AsyncMock(side_effect=execute_side_effect)
        else:
            mock_conn.execute = AsyncMock(return_value=None)

        mock_conn.commit = AsyncMock(return_value=None)
        mock_conn.rollback = AsyncMock(return_value=None)
        mock_conn.close = AsyncMock(return_value=None)

        return mock_conn, mock_get_applied

    # ------------------------------------------------------------------
    # apply — destructive guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_raises_on_destructive(self) -> None:
        """apply() must raise MigrationError if is_destructive=True."""
        runner = _make_runner()
        bad_migration = _simple_migration(is_destructive=True)

        with pytest.raises(MigrationError, match="is_destructive"):
            await runner.apply(bad_migration)

    # ------------------------------------------------------------------
    # apply — skip already applied
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_skips_already_applied(self) -> None:
        """apply() returns SKIPPED and executes no SQL when version already applied."""
        runner = _make_runner()
        migration = _simple_migration(version="0001")

        mock_conn, mock_get_applied = self._patch_runner(
            runner, applied_versions={"0001"}
        )

        with (
            patch.object(runner, "get_applied_versions", mock_get_applied),
            patch.object(runner, "_get_connection", AsyncMock(return_value=mock_conn)),
        ):
            status = await runner.apply(migration)

        assert status == MigrationStatus.SKIPPED
        # No SQL should have been executed
        mock_conn.execute.assert_not_called()

    # ------------------------------------------------------------------
    # apply — successful apply
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_returns_applied_on_success(self) -> None:
        """apply() returns APPLIED and calls execute twice (DDL + INSERT)."""
        runner = _make_runner()
        migration = _simple_migration(version="0001")

        mock_conn, mock_get_applied = self._patch_runner(
            runner, applied_versions=set()
        )

        with (
            patch.object(runner, "get_applied_versions", mock_get_applied),
            patch.object(runner, "_get_connection", AsyncMock(return_value=mock_conn)),
        ):
            status = await runner.apply(migration)

        assert status == MigrationStatus.APPLIED
        # execute called at least twice: DDL inside transaction + INSERT record
        assert mock_conn.execute.call_count >= 2

    # ------------------------------------------------------------------
    # apply_all — stops on first failure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_all_stops_on_failure(self) -> None:
        """apply_all stops after the first FAILED migration."""
        runner = _make_runner()

        migrations = [
            _simple_migration(version="0001"),
            _simple_migration(version="0002"),
            _simple_migration(version="0003"),
        ]

        call_count = 0

        async def mock_apply(migration: Migration) -> MigrationStatus:
            nonlocal call_count
            call_count += 1
            if migration.version == "0001":
                raise MigrationError("Simulated DDL failure")
            return MigrationStatus.APPLIED

        with patch.object(runner, "apply", side_effect=mock_apply):
            results = await runner.apply_all(migrations)

        # Only the first migration should appear in results (stopped after failure)
        assert len(results) == 1
        assert results[0][0].version == "0001"
        assert results[0][1] == MigrationStatus.FAILED
        # apply was only called once
        assert call_count == 1

    # ------------------------------------------------------------------
    # apply_all — version ordering
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_all_applies_in_version_order(self) -> None:
        """apply_all applies migrations in ascending version order."""
        runner = _make_runner()

        # Intentionally unsorted input
        migrations = [
            _simple_migration(version="0003"),
            _simple_migration(version="0001"),
            _simple_migration(version="0002"),
        ]

        applied_order: list[str] = []

        async def mock_apply(migration: Migration) -> MigrationStatus:
            applied_order.append(migration.version)
            return MigrationStatus.APPLIED

        with patch.object(runner, "apply", side_effect=mock_apply):
            await runner.apply_all(migrations)

        assert applied_order == ["0001", "0002", "0003"]


# ---------------------------------------------------------------------------
# ECS deployment config tests
# ---------------------------------------------------------------------------


class TestEcsDeployment:
    """Tests for infra/deployment ECS config helpers."""

    def test_ecs_service_config_has_circuit_breaker(self) -> None:
        """build_ecs_service_config must include the circuit breaker dict."""
        config = DEPLOYMENT_CONFIGS["channel-gateway"]
        result = build_ecs_service_config(config)

        assert "deploymentConfiguration" in result
        cb = result["deploymentConfiguration"].get("deploymentCircuitBreaker")
        assert cb is not None, "deploymentCircuitBreaker key missing"
        assert cb.get("Enable") is True
        assert cb.get("Rollback") is True

    def test_ecs_service_config_circuit_breaker_absent_when_disabled(self) -> None:
        """build_ecs_service_config omits circuit breaker when disabled."""
        config = DeploymentConfig(
            service_name="test-svc",
            strategy=RolloutStrategy.ROLLING,
            min_healthy_percent=50,
            max_percent=200,
            deployment_circuit_breaker=False,
        )
        result = build_ecs_service_config(config)
        assert "deploymentCircuitBreaker" not in result.get("deploymentConfiguration", {})

    def test_appspec_yaml_contains_service_name(self) -> None:
        """generate_appspec_yaml output must contain the service_name."""
        service_name = "api-server"
        task_def_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/api-server:42"
        yaml_out = generate_appspec_yaml(service_name, task_def_arn)

        assert service_name in yaml_out
        assert task_def_arn in yaml_out

    def test_appspec_yaml_contains_task_definition(self) -> None:
        """generate_appspec_yaml output must reference the task definition ARN."""
        arn = "arn:aws:ecs:us-east-1:111122223333:task-definition/my-svc:7"
        yaml_out = generate_appspec_yaml("my-svc", arn)
        assert arn in yaml_out

    def test_deployment_configs_cover_key_services(self) -> None:
        """DEPLOYMENT_CONFIGS must include api-server and channel-gateway."""
        assert "api-server" in DEPLOYMENT_CONFIGS
        assert "channel-gateway" in DEPLOYMENT_CONFIGS

    def test_api_server_uses_blue_green(self) -> None:
        """api-server deployment config must use BLUE_GREEN strategy."""
        assert DEPLOYMENT_CONFIGS["api-server"].strategy == RolloutStrategy.BLUE_GREEN

    def test_channel_gateway_min_healthy_100(self) -> None:
        """channel-gateway must maintain 100% healthy percent."""
        assert DEPLOYMENT_CONFIGS["channel-gateway"].min_healthy_percent == 100

    def test_build_deployment_circuit_breaker_returns_correct_dict(self) -> None:
        """build_deployment_circuit_breaker returns the expected ECS dict."""
        cb = build_deployment_circuit_breaker()
        assert cb == {"Enable": True, "Rollback": True}

    def test_ecs_service_config_blue_green_uses_code_deploy_controller(self) -> None:
        """Blue/green services must use CODE_DEPLOY controller type."""
        config = DEPLOYMENT_CONFIGS["api-server"]
        result = build_ecs_service_config(config)
        assert result["deploymentController"]["type"] == "CODE_DEPLOY"

    def test_ecs_service_config_rolling_uses_ecs_controller(self) -> None:
        """Rolling services must use ECS controller type."""
        config = DEPLOYMENT_CONFIGS["channel-gateway"]
        result = build_ecs_service_config(config)
        assert result["deploymentController"]["type"] == "ECS"
