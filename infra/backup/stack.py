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

"""graphclaw.infra.backup.stack — Backup stack descriptor and AWS resource generators.

Description
-----------
Provides two public callables consumed by deployment scripts and CDK/Terraform
modules:

- ``build_backup_stack`` assembles the full backup descriptor dict containing
  the policy catalogue, the runbook catalogue, and the AWS resource
  configuration required to provision backup infrastructure.
- ``generate_rds_backup_policy`` returns the AWS RDS parameter dict that
  satisfies the PRD Section 32.8 requirements for automated daily snapshots
  with 35-day retention and point-in-time recovery.

Design Patterns
---------------
- Pure functions: Neither function has side effects or imports cloud SDKs.
  Output dicts can be serialised directly to JSON for use with
  ``aws rds modify-db-instance`` or CDK ``rds.DatabaseInstance`` props.
- Data-driven: All values are derived from ``BACKUP_CONFIGS`` and
  ``RECOVERY_RUNBOOKS`` so the descriptor stays in sync with the catalogues
  automatically.

Public API
----------
- build_backup_stack() -> dict
- generate_rds_backup_policy() -> dict

Dependencies
------------
- ``infra.backup.configs`` — :data:`BACKUP_CONFIGS`.
- ``infra.backup.runbooks`` — :data:`RECOVERY_RUNBOOKS`.
- ``infra.backup.models`` — :class:`BackupTarget`.
"""

from __future__ import annotations

from infra.backup.configs import BACKUP_CONFIGS
from infra.backup.models import BackupTarget
from infra.backup.runbooks import RECOVERY_RUNBOOKS


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def build_backup_stack() -> dict:
    """Return the full backup stack descriptor.

    The returned dict has three top-level keys:

    ``configs``
        List of :class:`~infra.backup.models.BackupConfig` dataclasses from
        :data:`~infra.backup.configs.BACKUP_CONFIGS`.

    ``runbooks``
        List of :class:`~infra.backup.models.RecoveryRunbook` dataclasses from
        :data:`~infra.backup.runbooks.RECOVERY_RUNBOOKS`.

    ``aws_resources``
        Nested dict of AWS resource configuration dicts grouped by service.
        Currently contains ``rds`` (RDS backup policy) and ``s3``
        (S3 versioning policy for user-data and audit-log buckets).

    Returns
    -------
    dict
        Full backup stack descriptor suitable for use by deployment tooling.
    """
    rds_config = next(
        c for c in BACKUP_CONFIGS if c.target == BackupTarget.RDS_POSTGRES
    )
    s3_user_config = next(
        c for c in BACKUP_CONFIGS if c.target == BackupTarget.S3_USER_DATA
    )
    audit_config = next(
        c for c in BACKUP_CONFIGS if c.target == BackupTarget.AUDIT_LOG
    )

    return {
        "configs": BACKUP_CONFIGS,
        "runbooks": RECOVERY_RUNBOOKS,
        "aws_resources": {
            "rds": generate_rds_backup_policy(),
            "s3": {
                "user_data_bucket": {
                    "suffix": s3_user_config.s3_bucket_suffix,
                    "versioning_enabled": True,
                    "cross_region_replication": s3_user_config.cross_region,
                    "lifecycle_expiration_days": s3_user_config.retention_days,
                },
                "audit_log_bucket": {
                    "suffix": audit_config.s3_bucket_suffix,
                    "versioning_enabled": True,
                    "cross_region_replication": audit_config.cross_region,
                    "lifecycle_expiration_days": audit_config.retention_days,
                },
            },
        },
    }


def generate_rds_backup_policy() -> dict:
    """Return the AWS RDS automated backup configuration dict.

    All values satisfy PRD Section 32.8 requirements:
    - 35-day retention window.
    - Daily backup during the 03:00-04:00 timezone.utc low-traffic window.
    - Multi-AZ for automatic failover.
    - Encryption at rest.
    - Deletion protection enabled.
    - CloudWatch Logs exports for ``postgresql`` and ``upgrade`` log streams.

    Returns
    -------
    dict
        Parameter dict compatible with ``aws rds modify-db-instance`` and
        AWS CDK ``rds.DatabaseInstance`` props.
    """
    return {
        "BackupRetentionPeriod": 35,
        "PreferredBackupWindow": "03:00-04:00",  # timezone.utc low-traffic window
        "EnableCloudwatchLogsExports": ["postgresql", "upgrade"],
        "DeletionProtection": True,
        "MultiAZ": True,
        "StorageEncrypted": True,
    }
