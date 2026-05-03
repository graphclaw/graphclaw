# 13 — Tenancy Model: Organization & Workspace

**Status:** Draft v1.0 | **Date:** 2026-05-02

This document defines GraphClaw's two-tier tenancy model and how it serves both deployment scenarios:
- **On-prem per-org**: each organization deploys GraphClaw in its own cloud tenant; employees access it within the org.
- **SaaS multi-org**: many organizations share a deployment; users may belong to multiple orgs ("spheres"); strict isolation between unrelated orgs.

Companion docs: [12-intelligence-hub-architecture.md](12-intelligence-hub-architecture.md), [14-agent-triad.md](14-agent-triad.md), [15-user-identity-and-onboarding.md](15-user-identity-and-onboarding.md), [17-cross-tenant-task-projection.md](17-cross-tenant-task-projection.md). Source plan §9.8.2.5.

---

## 1. Two node types — already in the model

The model already defines both. **No new node types are needed** for any of the multi-tenant work in the requirements doc.

### OrganizationNode (`ORG-{uuid}`)
Source: [src/graphclaw/models/nodes.py:588](../../src/graphclaw/models/nodes.py#L588).

```python
class OrganizationNode(BaseNode):
    name: str
    domain: str | None        # e.g. "acme.com" — drives SSO matching
    owner_id: str             # USER-{uuid} of founding user
    members: list[OrgMember]  # membership list with roles
    settings: OrgSettings
```

**Role:** the **tenant / sphere boundary**. Membership, billing, SSO config, directory visibility, and cross-tenant ACL scoping all live here.

### WorkspaceNode (`WS-{uuid}`)
Source: [src/graphclaw/models/nodes.py:614](../../src/graphclaw/models/nodes.py#L614).

```python
class WorkspaceNode(BaseNode):
    org_id: str               # parent ORG-{uuid}
    name: str
    description: str
    visibility: WorkspaceVisibility
    task_prefix: str          # user-initials prefix for task IDs
    member_ids: list[str]     # subset of org members
    is_default: bool
```

**Role:** a **project-grouping inside an org** (e.g., "Engineering", "Q1 Launch", "Personal"). Tasks/goals are scoped here via `SCOPED_TO_WS` edges.

---

## 2. Mapping to deployment scenarios

| | On-prem per-org | SaaS multi-org |
|---|---|---|
| **OrganizationNode count** | One per deployment | Many — each user-created sphere |
| **Membership join** | OAuth via org domain (SSO) | Invitation, link-based join, or self-create |
| **`OrganizationNode.domain`** | Required for SSO domain match | Optional |
| **WorkspaceNode count** | Many per org (Engineering, Marketing, …) | Many per org |
| **Cross-org membership** | Rare/none | Common — user may belong to ORG-A and ORG-B |
| **Directory visibility default** | `open` (employees see colleagues) | `open` within an org; never across orgs |

The same model serves both — only org-creation flows and SSO defaults differ.

---

## 3. Scoping rules — where the boundary applies

### 3.1 User directory (per [arch/15](15-user-identity-and-onboarding.md))
- Indexed per `(user_id, org_id)`.
- `resolve_user(query)` scopes to **orgs that BOTH the calling user and the candidate share**.
- A user not in any of caller's orgs is **invisible** to the caller's resolution.

### 3.2 Cross-tenant task index (per [arch/17](17-cross-tenant-task-projection.md))
- Indexed per `(task_id, org_id, workspace_id, assignee_linked_user_ids[])`.
- `list_external_assignments_for_me()` scopes to `assignee.linked_user_id == caller.user_id AND org_id IN caller.orgs`.
- ACL enforced **at the repository layer** (FR-AL-001); cannot be bypassed by application code.

### 3.3 Task assignment (FR-XT-005)
- A task in `WS-engineering` (under `ORG-A`) can only be assigned to a UserNode-linked resource if that UserNode is a member of `ORG-A`.
- Pure ResourceNodes (no `linked_user_id`) — the external-party path — have no org constraint.

### 3.4 Workspace-aware delegation (FR-ID-002)
- `resolve_user` ranks candidates: workspace members first → org members → never returns users outside the org.

---

## 4. Membership lifecycle

### 4.1 Add member
- Admin adds via [api/admin/members.py](../../src/graphclaw/api/admin/members.py).
- Cascade (FR-AK-001):
  - User-directory row inserted for `(user_id, org_id)`.
  - Org-task-index gains visibility scope.
  - Existing ResourceNode shadows in other users' substrates with `linked_user_id == user_id` may detect via reconciliation.

### 4.2 Remove member
- Admin removes via [api/admin/members.py](../../src/graphclaw/api/admin/members.py).
- Cascade:
  - User-directory row archived (NOT deleted — see [arch/19](19-data-lifecycle-and-deletion-policy.md)).
  - Org-task-index entries no longer show in `list_external_assignments_for_me` for the removed user (filter `org_id IN bob.orgs`).
  - ResourceNode shadows pointing to the removed user → `link_status = detached_org_left` (FR-AD-001).
  - Tasks owned by the removed user are **NOT auto-reassigned**; admin chooses promote-to-org-owner or archive.

### 4.3 Org archive (SaaS — sphere shut down)
- Per FR-DEL-009: archiving an `OrganizationNode` does NOT cascade to member UserNodes.
- Members offered: join another org / become standalone (free tier) / self-archive.
- Workspaces in the org are archived but tasks remain readable to admin until purge.

---

## 5. Directory visibility policy

`OrganizationNode.settings.directory_visibility` (new — FR-GRAPH-006):

| Value | Behavior |
|---|---|
| `open` | Any org member can see + link any other member |
| `name-only` | Members visible by name only; identities hidden until consent |
| `consent-required` | Linking requires target's consent |
| `invitation-only` | Discovery disabled; only invited links work |

Per-user override: `UserNode.preferences.discoverability` can downgrade (never upgrade) the org default.

Default: `open`. Matches Slack/Teams convention.

---

## 6. Cross-org user model (SaaS)

Carol is in `ORG-A` and `ORG-B`. Her `UserNode` is **single** — she has one substrate at `{carol_user_id}/`. Her `OrganizationNode.members` entry exists in two orgs. The user-directory index has **two rows** — one per `(carol_user_id, org_id)` — because visibility settings can differ per org.

Resolution scoping rule: User-1 (in ORG-A only) sees Carol via her ORG-A directory row. User-3 (in ORG-B only) sees Carol via her ORG-B directory row. Neither can see the other org's row.

---

## 7. Files (current implementation)

| Concern | File |
|---|---|
| OrganizationNode model | [src/graphclaw/models/nodes.py:588](../../src/graphclaw/models/nodes.py#L588) |
| WorkspaceNode model | [src/graphclaw/models/nodes.py:614](../../src/graphclaw/models/nodes.py#L614) |
| Provisioning (creates UserNode + WorkspaceNode + JWTs) | [src/graphclaw/auth/provisioning.py](../../src/graphclaw/auth/provisioning.py) |
| Org-scoped admin endpoints | [src/graphclaw/api/admin/members.py](../../src/graphclaw/api/admin/members.py) |
| Cockpit org switcher (existing) | cockpit `docs/prd/02-graph-cockpit.md:151` |
| Cockpit org settings | cockpit `docs/prd/05-settings-panel.md` |

## 8. Files to add (per requirements)

| FR | File | Purpose |
|---|---|---|
| FR-GRAPH-006 | new migration | `OrganizationNode.settings.directory_visibility` |
| FR-DIR-001 | `src/graphclaw/identity/directory.py` | Org-scoped user directory read API |
| FR-DIR-001 | `src/graphclaw/identity/directory_indexer.py` | Event-bus consumer that updates per-org rows |
| FR-DIR-002 | `src/graphclaw/identity/resolver.py` | Cross-org membership scoping |
| FR-AK-001 | `src/graphclaw/cascade/membership.py` | Membership-change cascade |
| FR-DEL-009 | `src/graphclaw/api/admin/org_lifecycle.py` | Org archive flow |
| FR-UI-002 | `cockpit/src/features/auth/OrgSwitcher.tsx` | SaaS org switcher |
