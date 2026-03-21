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

"""graphclaw.infra.backup.configs — Backup policy catalogue for all GraphClaw data targets.

Description
-----------
Module-level constant ``BACKUP_CONFIGS`` enumerates every backup policy
required by PRD Section 32.8.  Each entry is an immutable :class:`BackupConfig`
describing the target, retention window, snapshot frequency, and SLA objectives
(RPO/RTO).

Design Patterns
---------------
- Data-driven configuration: Policies are expressed as plain dataclasses so
  they can be iterated by deployment scripts, CDK stacks, or Terraform modules
  without importing cloud SDKs at import time.
- Single source of truth: All backup policies live here; ``__init__.py``
  re-exports the public symbol so callers import from ``infra.backup`` directly.

Public API
----------
- BACKUP_CONFIGS: Ordered list of :class:`BackupConfig` for all four targets.

Dependencies
------------
- ``infra.backup.models`` — :class:`BackupConfig`, :class:`BackupTarget`.
"""

from __future__ import annotations

from infra.backup.models import BackupConfig, BackupTarget


# ---------------------------------------------------------------------------
# Backup policy catalogue
# PRD Sec 32.8: RDS automated snapshots, 35-day retention, S3 versioning
# ---------------------------------------------------------------------------

BACKUP_CONFIGS: list[BackupConfig] = [
    BackupConfig(
        target=BackupTarget.RDS_POSTGRES,
        retention_days=35,
        frequency_hours=24,
        pitr_enabled=True,
        cross_region=True,
        rpo="1h",
        rto="1h_rto",
    ),
    BackupConfig(
        target=BackupTarget.S3_USER_DATA,
        retention_days=90,
        frequency_hours=24,
        pitr_enabled=False,
        cross_region=True,
        rpo="24h",
        rto="4h_rto",
        s3_bucket_suffix="user-data-backups",
    ),
    BackupConfig(
        target=BackupTarget.REDIS_AOF,
        retention_days=7,
        frequency_hours=1,
        pitr_enabled=False,
        rpo="1h",
        rto="1h_rto",
    ),
    BackupConfig(
        target=BackupTarget.AUDIT_LOG,
        retention_days=365,  # compliance: 1 year
        frequency_hours=24,
        pitr_enabled=False,
        cross_region=True,
        rpo="24h",
        rto="4h_rto",
    ),
]
