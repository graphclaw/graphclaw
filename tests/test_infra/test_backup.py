# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for infra.backup — backup policies, recovery runbooks, and stack descriptors."""

from __future__ import annotations

import pytest

from infra.backup.configs import BACKUP_CONFIGS
from infra.backup.models import BackupTarget
from infra.backup.runbooks import RECOVERY_RUNBOOKS
from infra.backup.stack import build_backup_stack, generate_rds_backup_policy

# ---------------------------------------------------------------------------
# Backup config catalogue tests
# ---------------------------------------------------------------------------


def test_all_backup_targets_configured() -> None:
    """Every BackupTarget enum value must have at least one BackupConfig entry."""
    configured_targets = {c.target for c in BACKUP_CONFIGS}
    for target in BackupTarget:
        assert target in configured_targets, (
            f"BackupTarget.{target.name} has no corresponding BackupConfig entry"
        )


def test_rds_retention_35_days() -> None:
    """RDS Postgres backup config must enforce a 35-day retention window (PRD Sec 32.8)."""
    rds_config = next((c for c in BACKUP_CONFIGS if c.target == BackupTarget.RDS_POSTGRES), None)
    assert rds_config is not None, "No BackupConfig found for RDS_POSTGRES"
    assert rds_config.retention_days == 35, (
        f"Expected retention_days=35, got {rds_config.retention_days}"
    )


def test_rds_pitr_enabled() -> None:
    """RDS Postgres backup config must have pitr_enabled=True for point-in-time recovery."""
    rds_config = next((c for c in BACKUP_CONFIGS if c.target == BackupTarget.RDS_POSTGRES), None)
    assert rds_config is not None, "No BackupConfig found for RDS_POSTGRES"
    assert rds_config.pitr_enabled is True, "RDS Postgres backup config must have pitr_enabled=True"


def test_audit_log_retention_365_days() -> None:
    """Audit log backup must have a 365-day retention window (compliance requirement)."""
    audit_config = next((c for c in BACKUP_CONFIGS if c.target == BackupTarget.AUDIT_LOG), None)
    assert audit_config is not None, "No BackupConfig found for AUDIT_LOG"
    assert audit_config.retention_days == 365, (
        f"Expected retention_days=365 for compliance, got {audit_config.retention_days}"
    )


# ---------------------------------------------------------------------------
# Recovery runbook catalogue tests
# ---------------------------------------------------------------------------


def test_all_runbooks_defined() -> None:
    """RECOVERY_RUNBOOKS must contain exactly 4 runbooks covering all scenarios."""
    assert len(RECOVERY_RUNBOOKS) == 4, (
        f"Expected 4 recovery runbooks, got {len(RECOVERY_RUNBOOKS)}"
    )


def test_runbook_steps_ordered() -> None:
    """Every runbook's steps must be numbered sequentially starting from 1."""
    for runbook in RECOVERY_RUNBOOKS:
        step_numbers = [s.step_number for s in runbook.steps]
        expected = list(range(1, len(step_numbers) + 1))
        assert step_numbers == expected, (
            f"Runbook '{runbook.name}' has non-sequential step_numbers: "
            f"{step_numbers} (expected {expected})"
        )


def test_postgres_runbook_has_verification() -> None:
    """The postgres_data_loss runbook must have a non-None verification_query."""
    postgres_runbook = next((r for r in RECOVERY_RUNBOOKS if r.name == "postgres_data_loss"), None)
    assert postgres_runbook is not None, (
        "No runbook named 'postgres_data_loss' found in RECOVERY_RUNBOOKS"
    )
    assert postgres_runbook.verification_query is not None, (
        "postgres_data_loss runbook must have a verification_query for post-restore checks"
    )


# ---------------------------------------------------------------------------
# AWS resource configuration tests
# ---------------------------------------------------------------------------


def test_rds_backup_policy_deletion_protection() -> None:
    """RDS backup policy must have DeletionProtection=True to prevent accidental drops."""
    policy = generate_rds_backup_policy()
    assert policy["DeletionProtection"] is True, (
        "RDS backup policy must set DeletionProtection=True"
    )


def test_rds_backup_policy_encrypted() -> None:
    """RDS backup policy must have StorageEncrypted=True for data-at-rest encryption."""
    policy = generate_rds_backup_policy()
    assert policy["StorageEncrypted"] is True, "RDS backup policy must set StorageEncrypted=True"


# ---------------------------------------------------------------------------
# Immutability tests
# ---------------------------------------------------------------------------


def test_backup_config_frozen() -> None:
    """BackupConfig instances must be immutable (frozen dataclass)."""
    config = BACKUP_CONFIGS[0]
    with pytest.raises((AttributeError, TypeError)):
        config.retention_days = 999  # type: ignore[misc]


def test_recovery_runbook_frozen() -> None:
    """RecoveryRunbook instances must be immutable (frozen dataclass)."""
    runbook = RECOVERY_RUNBOOKS[0]
    with pytest.raises((AttributeError, TypeError)):
        runbook.name = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stack descriptor tests
# ---------------------------------------------------------------------------


def test_build_backup_stack_keys() -> None:
    """build_backup_stack() must return a dict with configs, runbooks, aws_resources."""
    stack = build_backup_stack()
    assert "configs" in stack, "build_backup_stack() result missing 'configs' key"
    assert "runbooks" in stack, "build_backup_stack() result missing 'runbooks' key"
    assert "aws_resources" in stack, "build_backup_stack() result missing 'aws_resources' key"
