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

"""graphclaw.infra.backup.runbooks — Disaster-recovery runbooks for each backup target.

Description
-----------
Module-level constant ``RECOVERY_RUNBOOKS`` enumerates a tested recovery
procedure for every failure scenario identified in PRD Section 32.8.  Each
runbook is an immutable :class:`RecoveryRunbook` whose ordered steps can be
consumed by operators, automated remediation tools, or runbook-as-code
frameworks without modification.

Design Patterns
---------------
- Immutable runbooks: All runbooks and their steps are frozen dataclasses so
  they can be safely shared across modules and serialised without copying.
- Explicit step ordering: ``step_number`` is set explicitly on every
  :class:`RecoveryStep`; callers can sort by this field rather than relying
  on list position.

Public API
----------
- RECOVERY_RUNBOOKS: List of :class:`RecoveryRunbook` covering all four
  failure scenarios.

Dependencies
------------
- ``infra.backup.models`` — :class:`BackupTarget`, :class:`RecoveryStep`,
  :class:`RecoveryRunbook`.
"""

from __future__ import annotations

from infra.backup.models import BackupTarget, RecoveryRunbook, RecoveryStep


# ---------------------------------------------------------------------------
# Runbook 1: Postgres data loss
# ---------------------------------------------------------------------------

_POSTGRES_DATA_LOSS = RecoveryRunbook(
    name="postgres_data_loss",
    scenario="Accidental data deletion or corruption in the AGE graph or relational schema",
    target=BackupTarget.RDS_POSTGRES,
    rto="1h_rto",
    steps=(
        RecoveryStep(
            step_number=1,
            action="Stop api-server to halt all incoming writes",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service api-server --desired-count 0"
            ),
            expected_output="Service api-server scaled to 0 tasks",
            rollback_command=(
                "aws ecs update-service "
                "--cluster graphclaw --service api-server --desired-count 2"
            ),
        ),
        RecoveryStep(
            step_number=2,
            action="Stop agent-runtime to halt all background graph mutations",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service agent-runtime --desired-count 0"
            ),
            expected_output="Service agent-runtime scaled to 0 tasks",
            rollback_command=(
                "aws ecs update-service "
                "--cluster graphclaw --service agent-runtime --desired-count 2"
            ),
        ),
        RecoveryStep(
            step_number=3,
            action=(
                "Restore database from the latest RDS automated snapshot "
                "or PITR timestamp"
            ),
            command=(
                "aws rds restore-db-instance-to-point-in-time "
                "--source-db-instance-identifier graphclaw-db "
                "--target-db-instance-identifier graphclaw-db-restored "
                "--restore-time <ISO8601_TIMESTAMP>"
            ),
            expected_output="Restored DB instance reaches 'available' status",
        ),
        RecoveryStep(
            step_number=4,
            action=(
                "Run init-db.sql against the restored instance if the schema "
                "is missing (e.g. after a blank restore)"
            ),
            command=(
                "psql $DATABASE_URL -f scripts/init-db.sql"
            ),
            expected_output="Extensions, graph labels, and embedding table created",
        ),
        RecoveryStep(
            step_number=5,
            action="Verify data integrity using UserNode count query",
            command=(
                "psql $DATABASE_URL -c "
                "\"SELECT * FROM cypher('graphclaw', "
                "\\$\\$ MATCH (u:UserNode) RETURN count(u) \\$\\$) "
                "AS (cnt agtype);\""
            ),
            expected_output="Returns a non-negative integer row count",
        ),
        RecoveryStep(
            step_number=6,
            action="Restart api-server and agent-runtime services",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service api-server --desired-count 2 && "
                "aws ecs update-service "
                "--cluster graphclaw --service agent-runtime --desired-count 2"
            ),
            expected_output="Both services reach RUNNING state with healthy tasks",
        ),
    ),
    verification_query=(
        "SELECT * FROM cypher('graphclaw', "
        "$$ MATCH (u:UserNode) RETURN count(u) $$) AS (cnt agtype);"
    ),
)


# ---------------------------------------------------------------------------
# Runbook 2: Redis corruption
# ---------------------------------------------------------------------------

_REDIS_CORRUPTION = RecoveryRunbook(
    name="redis_corruption",
    scenario="Redis AOF file corruption or catastrophic key-space loss",
    target=BackupTarget.REDIS_AOF,
    rto="1h_rto",
    steps=(
        RecoveryStep(
            step_number=1,
            action="Stop trigger-engine to prevent stale trigger state from propagating",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service trigger-engine --desired-count 0"
            ),
            expected_output="Service trigger-engine scaled to 0 tasks",
            rollback_command=(
                "aws ecs update-service "
                "--cluster graphclaw --service trigger-engine --desired-count 2"
            ),
        ),
        RecoveryStep(
            step_number=2,
            action="Flush all keys from the corrupted Redis instance",
            command="redis-cli -u $REDIS_URL FLUSHALL",
            expected_output="OK",
            rollback_command=None,
        ),
        RecoveryStep(
            step_number=3,
            action="Restore the latest AOF backup from S3 to the Redis data directory",
            command=(
                "aws s3 cp s3://graphclaw-backups/redis/appendonly.aof.latest "
                "/var/lib/redis/appendonly.aof"
            ),
            expected_output="File downloaded successfully",
        ),
        RecoveryStep(
            step_number=4,
            action="Restart the Redis service to replay the restored AOF",
            command="systemctl restart redis || docker restart graphclaw-cache",
            expected_output="Redis process is up and listening on port 6379",
        ),
        RecoveryStep(
            step_number=5,
            action="Verify Redis is responsive",
            command="redis-cli -u $REDIS_URL PING",
            expected_output="PONG",
        ),
        RecoveryStep(
            step_number=6,
            action="Restart trigger-engine service",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service trigger-engine --desired-count 2"
            ),
            expected_output="Service trigger-engine reaches RUNNING state",
        ),
    ),
    verification_query="PING",
)


# ---------------------------------------------------------------------------
# Runbook 3: S3 user-prefix loss
# ---------------------------------------------------------------------------

_S3_PREFIX_LOSS = RecoveryRunbook(
    name="s3_prefix_loss",
    scenario="Accidental deletion or corruption of a user's S3 object prefix",
    target=BackupTarget.S3_USER_DATA,
    rto="4h_rto",
    steps=(
        RecoveryStep(
            step_number=1,
            action="Identify the affected user_id and their S3 prefix",
            command=(
                "aws s3 ls s3://graphclaw/users/<user_id>/ --recursive | wc -l"
            ),
            expected_output="Current object count (may be 0 if prefix was deleted)",
        ),
        RecoveryStep(
            step_number=2,
            action=(
                "List available backup versions in the cross-region backup bucket "
                "to identify the most recent consistent snapshot"
            ),
            command=(
                "aws s3api list-object-versions "
                "--bucket graphclaw-user-data-backups "
                "--prefix users/<user_id>/ "
                "--query 'Versions[?IsLatest==`true`].[Key,LastModified]'"
            ),
            expected_output="JSON list of latest object versions with timestamps",
        ),
        RecoveryStep(
            step_number=3,
            action="Restore the affected prefix from S3 versioning or backup bucket",
            command=(
                "aws s3 cp "
                "s3://graphclaw-user-data-backups/users/<user_id>/ "
                "s3://graphclaw/users/<user_id>/ "
                "--recursive"
            ),
            expected_output="All objects copied successfully",
        ),
        RecoveryStep(
            step_number=4,
            action="Verify the restored object count matches the backup source",
            command=(
                "aws s3 ls s3://graphclaw/users/<user_id>/ --recursive | wc -l"
            ),
            expected_output="Object count matches pre-loss count from backup listing",
        ),
    ),
    verification_query=None,
)


# ---------------------------------------------------------------------------
# Runbook 4: Full-stack recovery
# ---------------------------------------------------------------------------

_FULL_STACK_RECOVERY = RecoveryRunbook(
    name="full_stack_recovery",
    scenario="Complete environment loss requiring full rebuild and data restoration",
    target=BackupTarget.RDS_POSTGRES,
    rto="4h_rto",
    steps=(
        RecoveryStep(
            step_number=1,
            action=(
                "Restore Redis (cache) first — it holds ephemeral broker state "
                "needed by other services"
            ),
            command=(
                "aws s3 cp s3://graphclaw-backups/redis/appendonly.aof.latest "
                "/var/lib/redis/appendonly.aof && "
                "systemctl restart redis || docker restart graphclaw-cache"
            ),
            expected_output="Redis responding to PING",
        ),
        RecoveryStep(
            step_number=2,
            action="Restore graph-db (Postgres + AGE) from RDS snapshot or PITR",
            command=(
                "aws rds restore-db-instance-to-point-in-time "
                "--source-db-instance-identifier graphclaw-db "
                "--target-db-instance-identifier graphclaw-db-restored "
                "--restore-time <ISO8601_TIMESTAMP>"
            ),
            expected_output="Restored DB instance reaches 'available' status",
        ),
        RecoveryStep(
            step_number=3,
            action="Apply init-db.sql schema if missing on the restored graph-db instance",
            command="psql $DATABASE_URL -f scripts/init-db.sql",
            expected_output="Schema extensions and graph labels created successfully",
        ),
        RecoveryStep(
            step_number=4,
            action="Restore relational-db tables from the same RDS snapshot",
            command=(
                "aws rds restore-db-instance-from-db-snapshot "
                "--db-instance-identifier graphclaw-relational-restored "
                "--db-snapshot-identifier <SNAPSHOT_ID>"
            ),
            expected_output="Relational DB instance reaches 'available' status",
        ),
        RecoveryStep(
            step_number=5,
            action="Start api-server and verify the health endpoint responds",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service api-server --desired-count 2"
            ),
            expected_output="GET /health returns HTTP 200",
        ),
        RecoveryStep(
            step_number=6,
            action="Start channel-gateway and confirm inbound channel adapters initialise",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service channel-gateway --desired-count 2"
            ),
            expected_output="channel-gateway service reaches RUNNING state",
        ),
        RecoveryStep(
            step_number=7,
            action="Start trigger-engine and verify scheduled triggers are loaded",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service trigger-engine --desired-count 2"
            ),
            expected_output="trigger-engine service reaches RUNNING state",
        ),
        RecoveryStep(
            step_number=8,
            action="Start agent-runtime last, after all dependencies are healthy",
            command=(
                "aws ecs update-service "
                "--cluster graphclaw --service agent-runtime --desired-count 2"
            ),
            expected_output="agent-runtime service reaches RUNNING state",
        ),
    ),
    verification_query=(
        "SELECT * FROM cypher('graphclaw', "
        "$$ MATCH (u:UserNode) RETURN count(u) $$) AS (cnt agtype);"
    ),
)


# ---------------------------------------------------------------------------
# Public catalogue
# ---------------------------------------------------------------------------

RECOVERY_RUNBOOKS: list[RecoveryRunbook] = [
    _POSTGRES_DATA_LOSS,
    _REDIS_CORRUPTION,
    _S3_PREFIX_LOSS,
    _FULL_STACK_RECOVERY,
]
