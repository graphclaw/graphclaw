# GraphClaw Backup & Disaster Recovery

Implements PRD Section 32.8 — automated backup policies, point-in-time
recovery, and tested runbooks for all four GraphClaw data targets.

---

## Backup Targets

| Target | Module constant | Description |
|---|---|---|
| `RDS_POSTGRES` | `BackupTarget.RDS_POSTGRES` | Apache AGE graph-db and relational-db (same Postgres instance) |
| `S3_USER_DATA` | `BackupTarget.S3_USER_DATA` | Per-user S3 object prefixes (`s3://graphclaw/users/<user_id>/`) |
| `REDIS_AOF` | `BackupTarget.REDIS_AOF` | Redis Append-Only File — broker queues and trigger state |
| `AUDIT_LOG` | `BackupTarget.AUDIT_LOG` | Compliance audit trail (structured JSON log archive) |

---

## Retention Policies

| Target | Retention | Frequency | PITR | Cross-region | RPO | RTO |
|---|---|---|---|---|---|---|
| RDS Postgres | 35 days | Daily (24 h) | Yes | Yes | 1 h | 1 h |
| S3 User Data | 90 days | Daily (24 h) | No | Yes | 24 h | 4 h |
| Redis AOF | 7 days | Hourly (1 h) | No | No | 1 h | 1 h |
| Audit Log | 365 days | Daily (24 h) | No | Yes | 24 h | 4 h |

The 365-day audit-log retention satisfies the compliance requirement to preserve
the full audit trail for at least one year.

---

## PITR Setup (RDS Postgres)

Point-in-time recovery is enabled on the RDS instance via `generate_rds_backup_policy()`:

- **Backup window:** 03:00–04:00 UTC (low-traffic)
- **Retention:** 35 days of automated snapshots
- **Multi-AZ:** Enabled for synchronous standby and automatic failover
- **Encryption:** `StorageEncrypted=True` (AES-256, AWS KMS)
- **Deletion protection:** `DeletionProtection=True` prevents accidental drops
- **CloudWatch log exports:** `postgresql` and `upgrade` streams

To restore to a specific timestamp:

```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier graphclaw-db \
  --target-db-instance-identifier graphclaw-db-restored \
  --restore-time 2024-06-15T04:30:00Z
```

---

## Tested Recovery Procedures

### 1. `postgres_data_loss` — RTO 1 h

Scenario: accidental data deletion or corruption in the graph or relational schema.

1. Scale api-server to 0 tasks (halt writes)
2. Scale agent-runtime to 0 tasks (halt graph mutations)
3. Restore from RDS PITR snapshot
4. Run `scripts/init-db.sql` if schema is absent on the restored instance
5. Verify with `SELECT * FROM cypher('graphclaw', $$ MATCH (u:UserNode) RETURN count(u) $$) AS (cnt agtype);`
6. Restart api-server and agent-runtime

### 2. `redis_corruption` — RTO 1 h

Scenario: Redis AOF corruption or catastrophic key-space loss.

1. Scale trigger-engine to 0 tasks
2. `FLUSHALL` the corrupted instance
3. Restore latest AOF backup from S3
4. Restart Redis to replay AOF
5. Verify with `PING` → expect `PONG`
6. Restart trigger-engine

### 3. `s3_prefix_loss` — RTO 4 h

Scenario: accidental deletion of a user's S3 object prefix.

1. Identify affected `user_id` and current object count
2. List available backup versions in `graphclaw-user-data-backups`
3. Restore from S3 versioning: `aws s3 cp s3://graphclaw-user-data-backups/users/<user_id>/ s3://graphclaw/users/<user_id>/ --recursive`
4. Verify restored object count matches backup source

### 4. `full_stack_recovery` — RTO 4 h

Scenario: complete environment loss requiring full rebuild.

Startup order (dependencies first):

1. Redis (cache)
2. graph-db (Postgres + AGE PITR restore)
3. Apply `scripts/init-db.sql` schema if missing
4. relational-db (same RDS snapshot)
5. api-server
6. channel-gateway
7. trigger-engine
8. agent-runtime

---

## RTO / RPO Targets

| Scenario | RPO | RTO |
|---|---|---|
| Postgres data loss | 1 h (PITR) | 1 h |
| Redis corruption | 1 h (hourly AOF) | 1 h |
| S3 prefix loss | 24 h (daily sync) | 4 h |
| Full stack recovery | 1 h (PITR) | 4 h |

---

## Module Layout

```
infra/backup/
  __init__.py     — re-exports public API
  models.py       — BackupTarget, RecoveryObjective, BackupConfig, RecoveryRunbook
  configs.py      — BACKUP_CONFIGS catalogue (4 entries)
  runbooks.py     — RECOVERY_RUNBOOKS catalogue (4 runbooks)
  stack.py        — build_backup_stack(), generate_rds_backup_policy()
  README.md       — this file
```

## Usage

```python
from infra.backup import BACKUP_CONFIGS, build_backup_stack

stack = build_backup_stack()
# stack["configs"]       — list[BackupConfig]
# stack["runbooks"]      — list[RecoveryRunbook]
# stack["aws_resources"] — dict with "rds" and "s3" sub-dicts
```
