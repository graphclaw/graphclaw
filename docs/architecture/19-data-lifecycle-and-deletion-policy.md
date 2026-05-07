# 19 — Data Lifecycle & Deletion Policy (No-Delete Principle)

**Status:** Draft v1.0 | **Date:** 2026-05-02 | **Priority:** Foundational — Wave 0

> **Principle:** No agent — main orchestrator, sub-agent, inbound, outbound, comms, or any future agent type — may ever perform a hard delete of any record. Agents may only **archive + tombstone**.

This is **first-class architecture**, not a feature. It changes how every Gap that involves removal is implemented. It is foundational and blocks all other waves.

Companion docs: companion banner in [agent-subagent-design-requirements.md](../agent-subagent-design-requirements.md). Source plan §9.8.16.5 + §9.8.17.

---

## 1. Rationale

- **GDPR compliance**: user-initiated "delete my data" must demonstrably remove data within a defined window — archive-then-purge satisfies this AND survives accidental triggers.
- **Catastrophic-action protection**: an LLM that can hallucinate a `delete_*` tool call is one bad turn from data loss. Removing the capability removes the failure mode entirely.
- **Auditability**: archived records remain accessible to admins for forensics, support, and dispute resolution.
- **Reversibility**: a 24h delay on user-initiated full purge gives a recovery window for catastrophic mistakes.

---

## 2. Service principal model (FR-DEL-001)

Three distinct DB / storage principals:

| Principal | DB grants | MinIO grants | Used by |
|---|---|---|---|
| `agent_principal` | SELECT, INSERT, UPDATE — **no DELETE** | GetObject, PutObject — **no DeleteObject** | All agents (comms, inbound, outbound, sub-agents, skills accessing GraphClaw DB) |
| `admin_principal` | Full (incl. DELETE) | Full | Purge worker; explicit cockpit Admin actions |
| `migration_principal` | DDL grants — **no DML DELETE** | none | Migration runner only |

### 2.1 Enforcement at the database layer
- Postgres: `REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM agent_principal;` for every user table.
- AGE: label-level grants ensure no delete vertex/edge for `agent_principal`.
- MinIO: bucket policy `Effect: Deny, Action: s3:DeleteObject` for the agent role.

### 2.2 Startup-time assertion
Agent processes execute on init:
```sql
BEGIN;
DELETE FROM _principal_probe WHERE 1=0;
ROLLBACK;
```
against a dedicated probe table. **Must raise `InsufficientPrivilege`**. If it does not, the process refuses to start.

### 2.3 Structured logging
Every DB call logs `principal_name`. Alert on any agent-context call using `admin_principal`.

### 2.4 Credential isolation
- Admin principal credentials live in a separate secrets namespace (`admin_secrets`), never in the same env namespace agents read from.
- Debug surfaces (e.g., SQL shell) use admin_principal but require interactive human re-auth via cockpit admin role; never callable by agents.

---

## 3. Semantic-delete prevention (FR-DEL-002)

Without this, the principle is bypassable via `update_task(state="PURGED", archived_at=now, purge_after=now-1s)`. **Highest priority.**

### 3.1 Lifecycle fields are admin-only
On every node table, lifecycle fields are write-restricted at the schema/trigger level when session role is `agent_principal`:
- `archived_at`, `archived_by`, `archive_reason`
- `purge_after`
- `link_status`
- `legal_hold`, `hold_reason`, `hold_set_by`, `hold_set_at`

### 3.2 Postgres trigger
```sql
CREATE OR REPLACE FUNCTION prevent_lifecycle_field_update()
RETURNS TRIGGER AS $$
BEGIN
  IF current_user = 'agent_principal' THEN
    IF NEW.archived_at IS DISTINCT FROM OLD.archived_at
       OR NEW.purge_after IS DISTINCT FROM OLD.purge_after
       OR NEW.link_status IS DISTINCT FROM OLD.link_status
       OR NEW.legal_hold IS DISTINCT FROM OLD.legal_hold THEN
      RAISE EXCEPTION 'Lifecycle fields cannot be updated by agent_principal';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to every node table.
```

### 3.3 Forbidden state values
Agents cannot transition tasks/goals into `PURGED` or `DELETED`. State machine validates against an allow-list per principal.

### 3.4 archive_* tools
Agent-callable `archive_task(task_id, reason)`, `archive_resource(resource_id, reason)`, `archive_goal(goal_id, reason)` — these post to an internal admin-service endpoint that uses `admin_principal` to compute and set lifecycle fields under controlled logic. Internal endpoint validates the agent's claim (`owner_id` match or has delegation).

---

## 4. Archive primitives (FR-DEL-003)

### 4.1 BaseNode extensions
```python
class BaseNode(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    # NEW lifecycle fields (admin-only writes):
    archived_at: datetime | None = None
    archived_by: str | None = None     # USER-{uuid} or "system"
    archive_reason: str | None = None
    purge_after: datetime | None = None
    legal_hold: bool = False
    hold_reason: str | None = None
    hold_set_by: str | None = None
    hold_set_at: datetime | None = None
```

### 4.2 TombstoneNode
```python
class TombstoneNode(BaseNode):
    archived_id: str               # the ID of the archived/redirected node
    redirect_to: str | None        # canonical replacement, or null for hard purge
    archived_at: datetime
    archived_by: str
    reason: str
```

### 4.3 resolve_canonical (FR-DEL-003)
```python
def resolve_canonical(node_id: str, max_hops: int = 5) -> str:
    """Follow tombstone redirects to canonical id.
    Raises TombstoneCycle on circular redirect, TombstoneChainTooDeep beyond max_hops."""
```
All read paths use this resolver before fetching.

### 4.4 Default read filter
Repository `get_node` and `query_nodes` apply `WHERE archived_at IS NULL` by default. Explicit `include_archived=True` flag for admin/forensic queries.

---

## 5. Tombstone integrity

### 5.1 Multi-hop chain (DEL-12)
A → B → C: `resolve_canonical(A)` returns `C` in single call. Cycle detection raises `TombstoneCycle` after `max_hops`.

### 5.2 Conversation-path redirect on merge (FR-RES-005 / DEL-13)
When `merge_resource(keep_id=Mr.Smith, merge_id=Bob)`:
- Bob's `.jsonl` files **append-merge-sorted by ts** into Mr.Smith's same-channel paths.
- Tiny `.tombstone` redirect file written at Bob's old conversation path.
- Original Bob `.jsonl` files **archived** (not deleted).

---

## 6. User-initiated full purge (FR-DEL-004)

Cockpit "Delete all my data" flow:
1. **Step 1 — Synchronous archive**: everything in `{user_id}/` substrate marked `archived_at = now, purge_after = now + 24h, archive_reason = 'user_purge'`.
2. **Step 2 — Banner**: cockpit shows "Your data will be permanently deleted in 24h — Cancel" banner.
3. **Step 3 — 24h purge worker** (admin_principal): hard-deletes archived records past `purge_after` AND `legal_hold IS NOT TRUE` AND `purge_cancelled_at IS NULL`.
4. **Step 4 — GDPR audit entry**: immutable log entry written.

### 6.1 Pending-purge active-user UX (FR-DEL-004 / DEL-14)
If a user with pending purge attempts to sign in, cockpit blocks normal access and shows a single blocking screen:
> "Your data is scheduled for deletion in Xh Ym."
> [Cancel deletion] [Continue with deletion]

No app surface is loaded until the user picks. Cancel writes `purge_cancelled_at = now` and reverts archive flags via admin_principal.

### 6.2 Race protection (DEL-15)
Purge worker re-checks `purge_after`, `legal_hold`, `purge_cancelled_at` immediately before each delete, **inside the same transaction**. The 30s cancel-vs-purge race becomes a read-your-write within a single txn.

---

## 7. Right to Erasure (FR-DEL-006)

GDPR Article 17 doesn't require a delay; the 24h window is a UX safety net.

A separate cockpit flow (`/settings/right-to-erasure`):
- Routes through admin_principal directly.
- Requires re-authentication within last 5 min.
- Captures justification field.
- Writes immutable audit-log entry.
- Runs purge synchronously before response returns.

Distinguished from the standard 24h flow in audit log.

---

## 8. Legal hold (FR-DEL-007)

Records carry `legal_hold` flag. Set/release only via admin_principal with audit entries.

Purge worker filters:
```sql
WHERE purge_after < now()
  AND legal_hold IS NOT TRUE
  AND purge_cancelled_at IS NULL
```

User informed (per their jurisdiction's rules) that erasure is paused.

---

## 9. Right to Access (DEL-18)

Data export tool reads across **archived AND active** records (ignores `archived_at IS NULL` filter for export). Export runs via admin_principal on user's own request. Response includes archive flags so the user understands which records are pending purge.

---

## 10. Infrastructure-level guards (FR-DEL-008)

The principle protects the application layer but not infrastructure. Forbid:
- Memgraph/AGE TTL on user data labels.
- MinIO bucket lifecycle expiry rules covering `users/*`.
- Postgres autovacuum FULL with row removal on user tables.

### 10.1 Startup config audit
Process refuses to start if:
- Any MinIO bucket has lifecycle rule covering `users/*`.
- Any AGE label has TTL configured.

Implementation: `src/graphclaw/observability/startup_audit.py` runs config-fetch + assert before service registration.

---

## 11. Org-archive does NOT cascade (FR-DEL-009)

Archiving an `OrganizationNode` does NOT touch member UserNodes. Members are offered:
- Join another org
- Become standalone (free-tier)
- Self-archive

Workspaces inside the archived org are also archived but their tasks remain readable to admin until purge.

Per [arch/13-tenancy-model.md §4.3](13-tenancy-model.md).

---

## 12. Purge worker (FR-DEL-005)

### 12.1 Schedule
Cron worker run via admin_principal. Default: every hour (configurable). Uses common heartbeat util `src/graphclaw/workers/heartbeat.py`.

### 12.2 DLQ + alerting (FR-AQ / Gap AQ)
Heartbeat written every run. Admin paged if heartbeat absent >2× expected interval.

On resume: catch-up batch with rate limit; audit-log entries record late processing.

### 12.3 Idempotency
Each delete is wrapped in a transaction. Concurrent worker invocations protected by advisory lock.

---

## 13. Scope clarification (Gap AO)

The No-Delete principle applies to **GraphClaw's own persistent state** (graph, storage, indices). It does **not** govern external-system mutations:
- Skills/MCP tools that delete a calendar event, an email message, or any external resource — these are governed by per-tool authorization and the user's connector consent.
- The user explicitly authorising "remove this meeting from my calendar" is intended; No-Delete is not violated.

This boundary is documented at the top of every skill/MCP integration guide.

---

## 14. Skill runtime principal (FR-AP / Gap AP)

Skills accessing GraphClaw's own DB or storage MUST use `agent_principal` — NOT a broader principal of convenience. Skills' external-system credentials are separate and per-skill.

Enforced via skill-runtime config audit + integration test.

---

## 15. Anti-delete probe tests

Required in CI and on every deploy:
- For every agent type and every tool, test that any deletion attempt surfaces as `InsufficientPrivilege` at the principal layer (not just at the application layer).
- Probe table technique: `BEGIN; DELETE FROM _principal_probe WHERE 1=0; ROLLBACK;` — must fail.
- Test that lifecycle field updates from agent_principal raise schema-level errors.

NFR-005 — anti-delete probe MUST run on every deploy and on every nightly schedule.

---

## 16. Files

### To create / modify (Wave 0)
| FR | File | Action |
|---|---|---|
| FR-DEL-001 | new `src/graphclaw/auth/principals.py` | Principal definitions, secret resolution, startup assertion |
| FR-DEL-001 | new `infrastructure/postgres/init/grants.sql` | REVOKE DELETE from agent role |
| FR-DEL-001 | new `infrastructure/minio/policies/agent-policy.json` | Deny s3:DeleteObject for agent role |
| FR-DEL-001 | [src/graphclaw/db/age/repository.py](../../src/graphclaw/db/age/repository.py) | Accept principal in connection factory |
| FR-DEL-001 | [src/graphclaw/infra/storage.py](../../src/graphclaw/infra/storage.py) | Accept principal in S3StorageClient |
| FR-DEL-001 | [src/graphclaw/api/deps.py](../../src/graphclaw/api/deps.py) | Inject agent_principal into agent code paths |
| FR-DEL-002 | [src/graphclaw/models/base.py](../../src/graphclaw/models/base.py) | BaseNode lifecycle fields with admin-only markers |
| FR-DEL-002 | [src/graphclaw/db/age/repository.py](../../src/graphclaw/db/age/repository.py) | `update_node` strips lifecycle fields when caller is agent_principal |
| FR-DEL-002 | new migration `0XX_lifecycle_fields_and_triggers.py` | Add columns + trigger function |
| FR-DEL-002 | new `src/graphclaw/agent/tools/archive.py` | archive_task / archive_resource / archive_goal tools |
| FR-DEL-002 | [src/graphclaw/state/machine.py](../../src/graphclaw/state/machine.py) | Forbidden state values list |
| FR-DEL-002 | [src/graphclaw/agent/tool_registry.py](../../src/graphclaw/agent/tool_registry.py) | Register archive tools; remove any delete_* |
| FR-DEL-003 | [src/graphclaw/models/base.py](../../src/graphclaw/models/base.py) | Lifecycle fields |
| FR-DEL-003 | [src/graphclaw/models/nodes.py](../../src/graphclaw/models/nodes.py) | TombstoneNode |
| FR-DEL-003 | new `src/graphclaw/db/age/redirects.py` | resolve_canonical primitive |
| FR-DEL-003 | new migration `0XX_tombstone_node.py` | TombstoneNode schema |
| FR-DEL-004 | [src/graphclaw/auth/routes.py](../../src/graphclaw/auth/routes.py) | Login returns 423 on pending purge |
| FR-DEL-004 | new `src/graphclaw/api/admin/lifecycle.py` | cancel-purge / confirm-purge endpoints |
| FR-DEL-004 | `cockpit/src/features/auth/PendingPurgeGate.tsx` | Blocking screen |
| FR-DEL-005 | new `src/graphclaw/workers/purge_worker.py` | Worker impl |
| FR-DEL-005 | new `src/graphclaw/workers/heartbeat.py` | Heartbeat util |
| FR-DEL-005 | new `infrastructure/k8s/cronjobs/purge-worker.yaml` | Schedule |
| FR-DEL-006 | `cockpit/src/features/settings/RightToErasureFlow.tsx` | Cockpit flow |
| FR-DEL-006 | new `src/graphclaw/audit/immutable_log.py` | Append-only audit log |
| FR-DEL-007 | [src/graphclaw/models/base.py](../../src/graphclaw/models/base.py) | legal_hold fields |
| FR-DEL-007 | new admin endpoints in [api/admin/lifecycle.py](../../src/graphclaw/api/admin/lifecycle.py) | set/release |
| FR-DEL-007 | `cockpit/src/features/admin/LegalHoldPanel.tsx` | Admin UI |
| FR-DEL-008 | new `src/graphclaw/observability/startup_audit.py` | Config audit |
| FR-DEL-009 | new `src/graphclaw/api/admin/org_lifecycle.py` | Org archive flow |
| FR-DEL-009 | `cockpit/src/features/admin/OrgArchiveFlow.tsx` | Admin UI |
| FR-AP | [src/graphclaw/skills/](../../src/graphclaw/skills/) | Skill runtime uses agent_principal |
| (probes) | new `tests/integration/test_no_delete_probes.py` | Anti-delete CI probes |
