# 17 — Cross-Tenant Task Projection (Approach A.1)

**Status:** Draft v1.0 | **Date:** 2026-05-02

When User-1 assigns a task to a counterparty Bob (where Bob is User-2 in the same org), Bob's own agent (Brian) must be able to see "tasks assigned to me by others" without us mirroring the task into Bob's substrate. This document specifies the **A.1 read-through approach** — single source of truth + org-level Postgres index + cross-tenant ACL.

A.2 (full mirror with sync) is **rejected for v1** but documented as a future option for offline-tolerant or privacy-isolated deployments.

Companion docs: [13-tenancy-model.md](13-tenancy-model.md), [14-agent-triad.md](14-agent-triad.md), [15-user-identity-and-onboarding.md](15-user-identity-and-onboarding.md). Source plan §9.8.7 + §9.8.8.

---

## 1. Principle

- **TSK-X stays in User-1's graph** as the canonical record.
- **No mirror task** in Bob's graph.
- An **org-level Postgres index** (`org_task_index`) projects minimum metadata across the tenant boundary.
- Bob's agent gets two read-only tools (FR-XT-002): `list_external_assignments_for_me`, `get_external_task_summary`.
- **Cross-tenant ACL** lives at the repository / query-builder layer (FR-AL-001 + FR-XT-003), enforced before any application code can see the data. Mirrors MinIO `{user_id}/` partitioning pattern.

---

## 2. Org task index (FR-XT-001)

```sql
CREATE TABLE org_task_index (
  task_id              TEXT PRIMARY KEY,
  owner_user_id        TEXT NOT NULL,
  org_id               TEXT NOT NULL,
  workspace_id         TEXT,
  assignee_linked_user_ids TEXT[] DEFAULT '{}',
  state                TEXT,
  deadline             TIMESTAMPTZ,
  last_activity_at     TIMESTAMPTZ,
  summary_text         TEXT,            -- redacted: title + 1-line summary only
  archived_at          TIMESTAMPTZ      -- archive flag (No-Delete principle)
);

CREATE INDEX org_task_index_assignees
  ON org_task_index USING gin (assignee_linked_user_ids)
  WHERE archived_at IS NULL;

CREATE INDEX org_task_index_org_state
  ON org_task_index (org_id, state)
  WHERE archived_at IS NULL;
```

### 2.1 Indexer (event-bus consumer)
Subscribes to `task.created`, `task.updated`, `task.state_changed`, `task.archived` events. Updates the index row within ≤5s p95 (NFR-003).

### 2.2 Reconciliation (FR-AE-001)
Nightly full-sync diff vs AGE source-of-truth. Admin endpoint for manual rebuild.

### 2.3 What does NOT cross the boundary
- Full task body, comments, intelligence log, history.
- Only minimum metadata + redacted summary.

---

## 3. Tools (FR-XT-002)

### 3.1 `list_external_assignments_for_me(filters?)`
```python
def list_external_assignments_for_me(
    state: list[TaskState] | None = None,
    deadline_before: datetime | None = None,
    workspace_id: str | None = None,
) -> list[ExternalAssignmentSummary]:
    """Return tasks owned by other users where I am an assignee.
    ACL filter applied automatically via caller_context."""
```

### 3.2 `get_external_task_summary(task_id)`
Returns redacted view:
```python
class ExternalTaskSummary(BaseModel):
    task_id: str
    owner_display_name: str
    org_name: str
    workspace_name: str | None
    title: str
    summary: str             # 1-line, no full body
    state: TaskState
    deadline: datetime | None
    last_activity_at: datetime
```

For full detail, the cockpit "Request access" flow is invoked (out of scope for v1; gated behind owner approval / delegation policy).

---

## 4. Cross-tenant ACL (FR-XT-003 + FR-AL-001)

### 4.1 Mandatory filter at repo layer
```python
def list_external_for_user(caller_context: CallerContext, ...) -> list[Row]:
    # Hard-coded in the query builder — cannot be bypassed.
    where = f"""
      assignee_linked_user_ids @> ARRAY[{caller_context.user_id}]::text[]
      AND org_id = ANY(:org_ids)
      AND archived_at IS NULL
    """
    return db.execute(query, {"org_ids": caller_context.org_ids})
```

### 4.2 ACLContextMissing exception
Repo calls without `caller_context` raise `ACLContextMissing` at query-build time — forces every call site to be explicit.

### 4.3 Mutation gating
State mutations on a task you don't own are gated through:
1. Owner's `delegation.md` policy `allowed_state_transitions` allow-list, OR
2. Otherwise — request flowed back to owner's comms agent as a `counterparty_proactive`-style message.

---

## 5. Org-scoped assignment validation (FR-XT-005)

At delegation time:
- Refuse `linked_user_id` whose UserNode isn't in the task's org.
- Surface friendly error: "Bob isn't in ORG-C — invite him first or pick a different person."
- Validation runs in the `update_task` and state-transition tools (gated by FR-AL-001 ACL).

---

## 6. Briefing integration (FR-XT-004)

Bob's daily briefing aggregator (Brian's `agent/briefing.py`):
1. Fetch local tasks (Bob owns).
2. Call `list_external_assignments_for_me()`.
3. Render unified briefing with **separate "Assigned by others" section** distinguished visually.

---

## 7. Privacy guarantees

| | Within ORG-A (User-1 + Bob both members) | Across orgs (User-1 in ORG-A, Bob only in ORG-B) |
|---|---|---|
| Org task index includes the row | Yes | Indexed for ORG-A and ORG-B independently |
| Bob's `list_external_…` returns it | Yes (matches `org_id IN bob.orgs`) | No (User-1 cannot assign to Bob in ORG-A; FR-XT-005 blocks) |
| Cross-org leak | Impossible by ACL | Impossible by ACL |

NFR-004 — zero-tolerance: cross-tenant query MUST NOT return data from non-shared orgs. ACL is mandatory and unit-tested per scenario.

---

## 8. Cascade interactions

### 8.1 Bob removed from org (ST-01 / FR-AK-001)
- Bob's UserNode → `OrganizationNode.members` updated.
- `org_task_index_assignees` query for Bob still returns the row, but ACL filter `org_id IN bob.orgs` excludes it.
- ResourceNode shadows in User-1's substrate flip `link_status` to `detached_org_left` (FR-AD-001).

### 8.2 Bob deletes account (ST-02)
- Bob's UserNode archived (per [arch/19](19-data-lifecycle-and-deletion-policy.md)).
- Shadow `link_status = detached_user_archived`; canonical fields frozen.
- `org_task_index` rows where Bob is assignee remain (task survives); Bob is no longer in `bob.orgs` so list returns nothing for the archived user.

### 8.3 Owner deletes account (ST-16 / FR-DEL-009)
- Owner's tasks archived; cross-tenant assignees see them disappear via `archived_at IS NULL` filter.
- Workspace/org admin can promote-to-org-owner before purge window expires.

---

## 9. A.2 fallback (NOT in v1)
For future regulated tenants prohibiting cross-tenant queries: a lightweight `MirroredTaskNode` is created in Bob's graph referencing TSK-X; status events propagate via the existing event bus. Decision deferred until an actual customer requirement surfaces.

---

## 10. Files

### To create
| FR | File | Purpose |
|---|---|---|
| FR-XT-001 | new `src/graphclaw/cross_tenant/task_index.py` | Index module |
| FR-XT-001 | new `src/graphclaw/cross_tenant/indexer.py` | Event consumer |
| FR-XT-001 | new migration `0XX_org_task_index.py` | Postgres table + indexes |
| FR-XT-002 | new `src/graphclaw/agent/tools/external_assignments.py` | Tool impls |
| FR-XT-003 | new `src/graphclaw/cross_tenant/repo.py` | Query builder with mandatory ACL |
| FR-XT-003 | new `src/graphclaw/cross_tenant/acl.py` | ACL helpers + tests |
| FR-XT-005 | [src/graphclaw/agent/tools/external_assignments.py](../../src/graphclaw/agent/tools/external_assignments.py) | Validate assignee org-membership |
| FR-XT-005 | [src/graphclaw/state/machine.py](../../src/graphclaw/state/machine.py) | Validate on state transitions |
| FR-XT-004 | [src/graphclaw/agent/briefing.py](../../src/graphclaw/agent/briefing.py) | Union local + external |
| FR-XT-004 | `cockpit/src/features/briefing/BriefingView.tsx` | Render external section |
| FR-AE-001 | new `src/graphclaw/cross_tenant/reconciler.py` | Nightly diff + admin rebuild |
| FR-AL-001 | [src/graphclaw/db/age/repository.py](../../src/graphclaw/db/age/repository.py) | Repo enforces caller_context everywhere |
| FR-AL-001 | [src/graphclaw/api/deps.py](../../src/graphclaw/api/deps.py) | Inject `caller_context` via FastAPI dep |
