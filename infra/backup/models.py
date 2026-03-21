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

"""graphclaw.infra.backup.models — Backup target enums and frozen configuration dataclasses.

Description
-----------
Defines the data model for the GraphClaw backup and disaster-recovery subsystem
(PRD Section 32.8).  All configuration objects are frozen dataclasses so they
can be embedded safely in module-level constants and shared across threads
without risk of accidental mutation.

Design Patterns
---------------
- Frozen dataclasses: ``BackupConfig`` and ``RecoveryRunbook`` are immutable
  at the Python level, preventing accidental field changes after construction.
- Enumerations: ``BackupTarget`` and ``RecoveryObjective`` encode the finite
  set of backup targets and SLA objectives; using Enum + str mixin allows
  values to be serialised directly to JSON/YAML without extra conversion.

Public API
----------
- BackupTarget: Enum of all data sources that must be backed up.
- RecoveryObjective: Enum of RPO/RTO SLA tiers used to tag configurations.
- BackupConfig: Frozen dataclass describing a single backup target's policy.
- RecoveryStep: Frozen dataclass representing one step in a recovery runbook.
- RecoveryRunbook: Frozen dataclass representing a full recovery procedure.

Dependencies
------------
- No third-party dependencies — pure Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class BackupTarget(str, Enum):
    """Identifies the data source being backed up."""

    RDS_POSTGRES = "rds_postgres"  # graph-db + relational-db
    S3_USER_DATA = "s3_user_data"  # user S3 prefixes
    REDIS_AOF = "redis_aof"        # Redis Append-Only File
    AUDIT_LOG = "audit_log"        # compliance audit trail


class RecoveryObjective(str, Enum):
    """RPO and RTO SLA tiers."""

    RPO_1H = "1h"       # Recovery Point Objective: 1 hour
    RPO_24H = "24h"     # Recovery Point Objective: 24 hours
    RTO_1H = "1h_rto"   # Recovery Time Objective: 1 hour
    RTO_4H = "4h_rto"   # Recovery Time Objective: 4 hours


# ---------------------------------------------------------------------------
# Backup configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupConfig:
    """Immutable backup policy for a single data target.

    Parameters
    ----------
    target:
        The data source covered by this policy.
    retention_days:
        Number of days backup snapshots are retained before expiry.
    frequency_hours:
        How often (in hours) a new backup snapshot is taken.
    pitr_enabled:
        Whether point-in-time recovery is enabled for this target.
    cross_region:
        Whether backup replicas are pushed to a secondary AWS region.
    rpo:
        Recovery Point Objective string (e.g. ``"1h"``).
    rto:
        Recovery Time Objective string (e.g. ``"4h_rto"``).
    s3_bucket_suffix:
        Suffix appended to the project bucket name when storing snapshots.
    """

    target: BackupTarget
    retention_days: int
    frequency_hours: int        # how often to back up
    pitr_enabled: bool = False  # point-in-time recovery
    cross_region: bool = False  # replicate to secondary region
    rpo: str = "24h"
    rto: str = "4h_rto"
    s3_bucket_suffix: str = "backups"


# ---------------------------------------------------------------------------
# Recovery runbook models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryStep:
    """A single numbered step within a disaster-recovery runbook.

    Parameters
    ----------
    step_number:
        1-based ordinal position of this step in the runbook.
    action:
        Human-readable description of the operation to perform.
    command:
        Optional shell command to execute (may be ``None`` for manual steps).
    expected_output:
        Optional description of the expected output or side effect.
    rollback_command:
        Optional shell command to undo this step if it must be reversed.
    """

    step_number: int
    action: str
    command: str | None = None
    expected_output: str | None = None
    rollback_command: str | None = None


@dataclass(frozen=True)
class RecoveryRunbook:
    """Immutable disaster-recovery runbook for a single failure scenario.

    Parameters
    ----------
    name:
        Short identifier for the runbook (e.g. ``"postgres_data_loss"``).
    scenario:
        Human-readable description of the failure scenario.
    target:
        The backup target this runbook restores.
    rto:
        Recovery Time Objective string for this runbook.
    steps:
        Ordered tuple of :class:`RecoveryStep` objects.
    verification_query:
        Optional SQL statement or shell command used to confirm a successful
        recovery.
    """

    name: str
    scenario: str
    target: BackupTarget
    rto: str
    steps: tuple[RecoveryStep, ...]
    verification_query: str | None = None
