# GraphClaw Requirements — Agent Triad, Communication Substrate, Identity & Lifecycle

**Status:** Draft v1.0 | **Date:** 2026-05-02 | **Source:** Plan `review-the-design-plans-squishy-eagle.md`

This document is the **tracked source of truth** for the design effort that extends GraphClaw's main orchestrator into a comms/inbound/outbound triad with multi-channel context, cross-user (counterparty) conversations, identity/onboarding, cross-tenant task projection, per-user policies, and the No-Delete data-lifecycle principle.

> **Foundational principle (Wave 0, blocks everything):** No agent ever performs a hard delete. Agent connections use a service principal with no DELETE grants at the database level. All "removal" is archive + tombstone; user-initiated full purge is a 24h-delayed admin operation. Implementation contract in [arch/19-data-lifecycle-and-deletion-policy.md](../architecture/19-data-lifecycle-and-deletion-policy.md).

Companion architecture docs:
- [arch/13-tenancy-model.md](../architecture/13-tenancy-model.md) — OrganizationNode / WorkspaceNode roles, on-prem vs SaaS deployments
- [arch/14-agent-triad.md](../architecture/14-agent-triad.md) — Comms / Inbound / Outbound peer-agent triad
- [arch/15-user-identity-and-onboarding.md](../architecture/15-user-identity-and-onboarding.md) — identities, aliases, linked_user_id, onboarding FSM, resolution
- [arch/16-cross-user-conversations.md](../architecture/16-cross-user-conversations.md) — counterparty-scoped storage, routing matrix
- [arch/17-cross-tenant-task-projection.md](../architecture/17-cross-tenant-task-projection.md) — A.1 read-through model, org task index
- [arch/18-follow-up-cadence.md](../architecture/18-follow-up-cadence.md) — scheduled follow-ups
- [arch/19-data-lifecycle-and-deletion-policy.md](../architecture/19-data-lifecycle-and-deletion-policy.md) — No-Delete contract

---

## 1. Glossary

| Term | Definition |
|---|---|
| **Comms Agent** | The main orchestrator agent, one per user (`agent_id == user_id`). User-facing reasoning, planning, delegation. |
| **Inbound Agent** | System-level agent (`InboundIntelligenceAgent`) that classifies and distills inbound messages from any channel. |
| **Outbound Agent** | System-level peer agent (`OutboundCommunicationAgent`, promoted from `OutboundDispatcher`) that handles all outbound dispatch with channel resolution, drafting, batching, CheckinNode + intelligence writes. |
| **Owner** | The user who owns a comms agent (`agent.owner_id == user_id`). |
| **Counterparty** | Any person other than the owner the comms agent talks to on the owner's behalf. May be a known UserNode (linked) or a pure ResourceNode (external). |
| **Receiving Account** | The channel-side address (Telegram bot, email mailbox, WhatsApp number) the agent owns; mapped to `(user_id, agent_id)` via `AgentChannelIdentity`. |
| **Thread** | A channel-specific conversation handle (Telegram chat id, email thread, WhatsApp chat). |
| **Distillation** | Post-turn extraction of `task_entry` (→ node intelligence) and `memory_note` (→ working memory). |
| **agent_principal / admin_principal / migration_principal** | Database service principals; see arch/19. |
| **Org / Workspace** | `OrganizationNode` is the tenant boundary; `WorkspaceNode` is a project-grouping inside an org. See arch/13. |

---

## 2. Functional requirements

Each FR carries: **ID**, **priority** (P0/P1/P2), **description**, **files to touch**, **implementation notes**, **acceptance criteria**, **dependencies**.

Notation: file paths are repo-relative. `cockpit/` refers to the sibling `graphclaw-cockpit` repo.

---

### 2.0 Foundational — No-Delete principle (Wave 0)

#### FR-DEL-001 — Service-principal split (P0)
**Description.** Three distinct DB/storage principals: `agent_principal` (no DELETE), `admin_principal` (full, used by purge worker + admin), `migration_principal` (DDL only, no DML DELETE).

**Files to touch.**
- `src/graphclaw/db/age/repository.py` — accept principal in connection factory
- `src/graphclaw/infra/storage.py` — accept principal in `S3StorageClient`
- `src/graphclaw/db/base.py` — principal-aware connection ABC
- `src/graphclaw/auth/principals.py` — **NEW**: principal definitions, secret resolution, startup-time assertions
- `src/graphclaw/api/deps.py` — inject `agent_principal` into all agent code paths; `admin_principal` only into admin/purge routes
- `infrastructure/postgres/init/grants.sql` — **NEW**: REVOKE DELETE from agent role; GRANT DELETE only to admin role
- `infrastructure/minio/policies/agent-policy.json` — **NEW**: deny `s3:DeleteObject` for agent role
- `docker-compose.yml` (graphclaw + cockpit) — separate secret env-vars per principal

**Implementation notes.**
- Connection-factory helper `get_repo(principal: Principal)` returns a graph repo bound to that principal's connection pool.
- Startup assertion: agent process executes `BEGIN; DELETE FROM _principal_probe WHERE 1=0; ROLLBACK;` against a dedicated probe table — must raise `InsufficientPrivilege`. If it doesn't, refuse to start.
- All DB calls log `principal_name` in structured logs.

**Acceptance.**
- AC1: Postgres role `agent_principal` has no DELETE grant on any user table; verified by query against `information_schema.role_table_grants`.
- AC2: Process startup raises if probe DELETE succeeds.
- AC3: Existing tests that create agents via `agent_principal` fail when calling `archive_*` if they pass already-set `archived_at` (covered by FR-DEL-002).
- AC4: Anti-delete probe test in CI for every agent + every tool — asserts deletion attempts surface as `InsufficientPrivilege`.

**Dependencies.** None — foundational.

---

#### FR-DEL-002 — Semantic-delete prevention (P0)
**Description.** Lifecycle fields (`archived_at`, `archived_by`, `archive_reason`, `purge_after`, `link_status`, `legal_hold`) are write-restricted to `admin_principal` at the schema level. Agents call `archive_*` tools that compute these fields server-side under controlled logic.

**Files to touch.**
- `src/graphclaw/models/base.py` — `BaseNode` adds lifecycle fields with `admin_only=True` markers
- `src/graphclaw/db/age/repository.py` — generic `update_node()` strips lifecycle fields when caller is `agent_principal`
- `src/graphclaw/migrations/catalogue.py` + new migration `004_lifecycle_fields.py` — add columns + Postgres triggers `RAISE EXCEPTION` on UPDATE of lifecycle columns when `current_user = 'agent_principal'`
- `src/graphclaw/agent/tools/archive.py` — **NEW**: `archive_task`, `archive_resource`, `archive_goal` tools that internally call admin-side helpers (via service interface, NOT direct admin_principal injection)
- `src/graphclaw/state/machine.py` — forbidden state values list: `PURGED`, `DELETED` not callable by agents
- `src/graphclaw/agent/tool_registry.py` — register archive tools in `task_management` tool set; remove any `delete_*` tool

**Implementation notes.**
- Postgres trigger function `prevent_lifecycle_field_update()` rejects UPDATE on lifecycle columns when session role is `agent_principal`. Same trigger on every node table.
- Agent-callable `archive_task(task_id, reason)` posts to an internal admin-service endpoint that uses `admin_principal` to compute and set fields. Internal endpoint validates the agent's claim (`owner_id == task.owner_id` or has delegation).

**Acceptance.**
- AC1: `update_task(task_id, archived_at=now())` from agent context raises `InsufficientPrivilege`.
- AC2: `update_task(task_id, state="PURGED")` raises `InvalidStateTransition`.
- AC3: `archive_task(task_id, reason="user_request")` succeeds; node's `archived_at` populated, `purge_after = now + 24h`, `archived_by = caller_user_id`.

**Dependencies.** FR-DEL-001.

---

#### FR-DEL-003 — Archive primitives + tombstone redirects (P0)
**Description.** Every node/edge/file gains lifecycle fields and a tombstone-redirect mechanism. `resolve_canonical(node_id)` resolver follows redirects with cycle detection and max-hop cap (default 5).

**Files to touch.**
- `src/graphclaw/models/base.py` — `archived_at`, `archived_by`, `archive_reason`, `purge_after` on `BaseNode`
- `src/graphclaw/models/nodes.py` — new `TombstoneNode { archived_id, redirect_to: node_id | null, archived_at, archived_by, reason }`
- `src/graphclaw/db/age/repository.py` — `resolve_canonical(node_id, max_hops=5)`; `get_node()` and `query_nodes()` apply `archived_at IS NULL` by default with `include_archived=False` flag
- `src/graphclaw/db/age/redirects.py` — **NEW**: tombstone resolver primitive
- `src/graphclaw/migrations/0XX_tombstone_node.py` — **NEW**

**Acceptance.**
- AC1: `resolve_canonical(A_id)` where A→B→C returns C in single call.
- AC2: Cycle detection raises `TombstoneCycle` after `max_hops`.
- AC3: Default reads exclude archived nodes; explicit `include_archived=True` returns them.

**Dependencies.** FR-DEL-001, FR-DEL-002.

---

#### FR-DEL-004 — Pending-purge active-user UX (P0)
**Description.** Cockpit blocks normal sign-in when user has pending purge; shows Cancel/Continue choice screen.

**Files to touch.**
- `cockpit/src/features/auth/PendingPurgeGate.tsx` — **NEW**
- `cockpit/src/lib/api-client.ts` — handle 423 Locked response with purge-state body
- `src/graphclaw/api/auth.py` — login endpoint returns 423 with `{purge_after, purge_initiated_at}` if user has pending purge
- `src/graphclaw/api/admin/lifecycle.py` — **NEW**: `POST /lifecycle/cancel-purge`, `POST /lifecycle/confirm-purge`

**Acceptance.**
- AC1: User with `purge_after IS NOT NULL` calling `/auth/login` gets 423 + payload.
- AC2: Cockpit shows blocking screen until user picks; no other route renders.
- AC3: Cancel writes `archived_at = NULL, purge_after = NULL` for user's substrate via admin_principal.

**Dependencies.** FR-DEL-001, FR-DEL-003.

---

#### FR-DEL-005 — Scheduled purge worker + DLQ + alerting (P0)
**Description.** Worker on `admin_principal` purges records past `purge_after` AND `legal_hold IS NOT TRUE` AND `purge_cancelled_at IS NULL`. Re-checks within transaction. Heartbeat + alerting if down >2× expected interval.

**Files to touch.**
- `src/graphclaw/workers/purge_worker.py` — **NEW**
- `src/graphclaw/workers/heartbeat.py` — **NEW** common heartbeat util
- `src/graphclaw/api/admin/lifecycle.py` — manual rebuild trigger
- `infrastructure/k8s/cronjobs/purge-worker.yaml` — **NEW** (scheduling)
- `src/graphclaw/observability/alerts.py` — alert rule for stale heartbeat

**Acceptance.**
- AC1: Worker hard-deletes archived records past `purge_after` using `admin_principal`.
- AC2: Worker filters out `legal_hold=true` records.
- AC3: Cancel race: cancel set at T-1s, worker fires at T → row not deleted (re-check inside txn).
- AC4: Heartbeat absence >2× interval triggers PagerDuty/Slack alert.

**Dependencies.** FR-DEL-003.

---

#### FR-DEL-006 — Right to Erasure (immediate purge) (P1)
**Description.** Separate cockpit flow for immediate (non-24h) purge per GDPR Article 17. Routes through admin_principal with re-auth + justification + immutable audit.

**Files to touch.**
- `cockpit/src/features/settings/RightToErasureFlow.tsx` — **NEW**
- `src/graphclaw/api/admin/lifecycle.py` — `POST /lifecycle/right-to-erasure`
- `src/graphclaw/audit/immutable_log.py` — **NEW** append-only audit log

**Acceptance.**
- AC1: Endpoint requires re-authentication within 5 min.
- AC2: Audit log entry written with user_id, timestamp, justification, admin_principal_actor_id.
- AC3: Synchronous purge completes before response returns.

**Dependencies.** FR-DEL-003, FR-DEL-005.

---

#### FR-DEL-007 — Legal hold (P1)
**Description.** Records can carry `legal_hold` flag set by admin; purge worker filters out held records.

**Files to touch.**
- `src/graphclaw/models/base.py` — add `legal_hold, hold_reason, hold_set_by, hold_set_at`
- `src/graphclaw/api/admin/lifecycle.py` — `POST/DELETE /lifecycle/legal-hold/{node_id}`
- `cockpit/src/features/admin/LegalHoldPanel.tsx` — **NEW**

**Acceptance.**
- AC1: Setting hold prevents purge worker from deleting.
- AC2: Set/release events go to immutable audit log.

**Dependencies.** FR-DEL-005.

---

#### FR-DEL-008 — Infrastructure-level deletion guards (P0)
**Description.** Forbid Memgraph/AGE TTL on user labels, MinIO bucket lifecycle expiry on user prefixes, Postgres autovacuum FULL with row removal. Startup config audit fails deployment on violation.

**Files to touch.**
- `src/graphclaw/observability/startup_audit.py` — **NEW** config audit
- `infrastructure/postgres/postgresql.conf` — autovacuum settings reviewed; comments locking down destructive defaults
- `infrastructure/minio/policies/lifecycle-audit.sh` — startup script that lists bucket lifecycle rules and fails if any expire `users/*`
- `docs/architecture/19-data-lifecycle-and-deletion-policy.md` — `## Infrastructure config requirements` section

**Acceptance.**
- AC1: Process refuses to start if MinIO bucket has lifecycle rule covering `users/*`.
- AC2: Process refuses to start if any AGE label has TTL configured.

**Dependencies.** FR-DEL-001.

---

#### FR-DEL-009 — Org-archive does NOT cascade to UserNodes (P1)
**Description.** Archiving an `OrganizationNode` does NOT touch member UserNodes. Members are offered: join another org, become standalone, or self-archive.

**Files to touch.**
- `src/graphclaw/api/admin/org_lifecycle.py` — **NEW**
- `cockpit/src/features/admin/OrgArchiveFlow.tsx` — **NEW**

**Acceptance.**
- AC1: Archiving ORG-X with members [USER-1, USER-2] leaves both UserNodes intact.
- AC2: Workspaces under ORG-X are archived but tasks remain readable to admin until purge.

**Dependencies.** FR-DEL-003, FR-DEL-005.

---

### 2.1 Tenancy & schema (Wave 1)

#### FR-GRAPH-001 — UserNode.identities + ResourceNode.identities (P0)
**Description.** Add channel-identity registries to user/resource nodes for inbound classification and outbound routing.

**Files to touch.**
- `src/graphclaw/models/nodes.py` — `UserNode.identities: ChannelIdentities`, `ResourceNode.identities: ChannelIdentities`
- `src/graphclaw/models/__init__.py` — export `ChannelIdentities`
- `src/graphclaw/migrations/0XX_node_identities.py` — **NEW** (JSONB column)
- `src/graphclaw/db/age/repository.py` — read/write `identities` field

**Schema.**
```python
class ChannelIdentities(BaseModel):
    emails: list[str] = []
    phones: list[str] = []
    telegram_id: str | None = None
    telegram_username: str | None = None
    whatsapp_id: str | None = None
    slack_user_id: str | None = None
```

**Acceptance.**
- AC1: Migration adds JSONB column with default `{}`; existing nodes unaffected.
- AC2: `AliasResolver.register(channel, sender_id, user_id)` also writes back to UserNode.identities.

**Dependencies.** None (Wave 1).

---

#### FR-GRAPH-002 — UserNode.aliases + ResourceNode.aliases (P0)
**Description.** Owner-specific nicknames separate from canonical `name`.

**Files to touch.**
- `src/graphclaw/models/nodes.py` — `aliases: list[AliasEntry] = []`
- `src/graphclaw/models/types.py` — **NEW** `AliasEntry { value: str, added_at, added_by, source: str }`
- `src/graphclaw/migrations/0XX_node_aliases.py`

**Acceptance.**
- AC1: Aliases are searchable by `resolve_user`.
- AC2: Provenance preserved.

**Dependencies.** FR-DEL-002.

---

#### FR-GRAPH-003 — ResourceNode.linked_user_id + link_status (P0)
**Description.** Cross-tenant shadow reference. When set, canonical preferences read through to the linked UserNode.

**Files to touch.**
- `src/graphclaw/models/nodes.py` — `ResourceNode.linked_user_id: str | None`, `link_status: LinkStatus`
- `src/graphclaw/models/enums.py` — `LinkStatus { active, detached_user_archived, detached_user_purged }`
- `src/graphclaw/db/age/repository.py` — `get_resource_with_linked_view(resource_id)` returns merged view
- `src/graphclaw/migrations/0XX_linked_user_id.py`

**Acceptance.**
- AC1: Resource with `linked_user_id=USER-X` returns USER-X's `preferences` and `identities` for read; owner-specific fields stay on the shadow.
- AC2: When linked UserNode is archived, `link_status` flips automatically (via cascade in FR-AK).

**Dependencies.** FR-GRAPH-001, FR-DEL-003.

---

#### FR-GRAPH-004 — CheckinNode field expansion (P0)
**Description.** Add `recipient_id`, `channel`, `thread_id`, `direction` for reply linking and conversation persistence.

**Files to touch.**
- `src/graphclaw/models/nodes.py` — extend `CheckinNode`
- `src/graphclaw/migrations/0XX_checkin_fields.py`
- `src/graphclaw/db/age/repository.py` — index on `(channel, thread_id)`
- Backfill: `scripts/backfill_checkin_fields.py` — best-effort populate from existing intelligence log lines

**Acceptance.**
- AC1: New fields populated by outbound agent on every send.
- AC2: Index supports `(channel, thread_id)` lookup in <10ms at 1M rows.

**Dependencies.** FR-DEL-002 (lifecycle fields admin-only).

---

#### FR-GRAPH-005 — UserNode.preferences extensions (P0)
**Description.** Add `discoverability`, `channel_stickiness_hours`, `channel_stickiness_overrides`. (Note: `delegation_policy` is **NOT** stored here — it's a MinIO `.md` file per FR-POL-001.)

**Files to touch.**
- `src/graphclaw/models/nodes.py` — extend `UserPreferences`
- `src/graphclaw/migrations/0XX_user_preferences.py`

**Schema additions.**
```python
discoverability: DiscoverabilityLevel = DiscoverabilityLevel.ORG_DEFAULT  # uses org default unless overridden
channel_stickiness_hours: int = 48
channel_stickiness_overrides: dict[str, int] = {}  # {"email": 168, "telegram": 24}
```

**Acceptance.**
- AC1: Outbound resolver consults these when stickiness applies.

**Dependencies.** None.

---

#### FR-GRAPH-006 — OrganizationNode.settings.directory_visibility (P0)
**Description.** Org-wide default for cross-user directory visibility.

**Files to touch.**
- `src/graphclaw/models/nodes.py` — extend `OrgSettings`
- `src/graphclaw/migrations/0XX_org_directory_visibility.py`

**Schema.**
```python
class OrgDirectoryVisibility(str, Enum):
    OPEN = "open"
    NAME_ONLY = "name-only"
    CONSENT_REQUIRED = "consent-required"
    INVITATION_ONLY = "invitation-only"

# In OrgSettings:
directory_visibility: OrgDirectoryVisibility = OrgDirectoryVisibility.OPEN
```

**Acceptance.**
- AC1: User-directory query filters per org's setting and per-user override.

**Dependencies.** None.

---

#### FR-STORE-001 — Counterparty-scoped conversation storage (P0)
**Description.** Replace flat `{user_id}/chat/history.json` with `{user_id}/conversations/{counterparty_id}/{channel}/{thread_id}.jsonl` + `index.json`. Owner-self conversations live under `conversations/{user_id}/...`.

**Files to touch.**
- `src/graphclaw/infra/storage.py` — new `StoragePaths` methods: `conversation_thread()`, `conversation_index()`, `conversation_counterparty_dir()`
- `src/graphclaw/api/chat.py` — refactor to use new paths; keep REST surface unchanged but persist channel-tagged
- `src/graphclaw/inbound/intelligence_agent.py` — write conversation entries
- `src/graphclaw/agent/outbound.py` — write conversation entries
- `scripts/migrate_chat_history.py` — **NEW**: one-shot migration of `chat/history.json` → `conversations/{user_id}/cockpit/{thread}.jsonl`

**Schema (jsonl entry).**
```json
{"message_id":"...","ts":"...","direction":"in|out","channel":"...","thread_id":"...","sender_id":"...","content":"...","task_refs":[...],"checkin_id":"..."}
```

**Acceptance.**
- AC1: Owner cockpit chat lands in `conversations/{user_id}/cockpit/{thread}.jsonl`.
- AC2: Inbound from counterparty Bob via Telegram lands in `conversations/{user_id}/{Bob_id}/telegram/{thread}.jsonl`.
- AC3: Migration script preserves all existing chat history with `channel="cockpit"` tag.

**Dependencies.** FR-DEL-001, FR-GRAPH-004.

---

#### FR-STORE-002 — Per-user policy MinIO files (P0)
**Description.** Create `{user_id}/agents/{agent_id}/policies/*.md` substrate with YAML frontmatter + markdown body. Frontmatter parsed by policy evaluator.

**Files to touch.**
- `src/graphclaw/infra/storage.py` — new `StoragePaths.agent_policy(user_id, agent_id, policy_name)`
- `src/graphclaw/agent/policies/__init__.py` — **NEW** module
- `src/graphclaw/agent/policies/evaluator.py` — **NEW**: parse YAML frontmatter via `pyyaml`; Pydantic schema per policy type
- `src/graphclaw/agent/policies/schemas.py` — **NEW**: `DelegationPolicy`, `EscalationPolicy`, `CounterpartyEtiquettePolicy`, `ReplyTonePolicy`
- `src/graphclaw/agent/policies/loader.py` — **NEW**: load + Redis cache (15min TTL, same as profile.md)
- `src/graphclaw/api/policies.py` — **NEW** REST endpoints for read/write per policy
- `templates/policies/*.md` — **NEW**: seed templates for each policy

**Schema (delegation.md frontmatter).**
```yaml
---
fail_mode: closed
auto_acknowledge: true
accept_deadline_extension_max_days: 3
allowed_state_transitions:
  - { from: WAITING, to: IN_PROGRESS }
escalate_on_blocker: true
recipient_overrides:
  CEO-001: { accept_deadline_extension_max_days: 0 }
---
# Body — narrative guidance for LLM
```

**Acceptance.**
- AC1: Policy file at canonical path; loader returns parsed schema + raw body.
- AC2: Frontmatter validated against Pydantic schema; bad schema raises clear error.
- AC3: 15min Redis cache; cache invalidation on POST.
- AC4: `fail_mode: closed` → load failure causes counterparty_conversation to refuse outbound.

**Dependencies.** FR-DEL-001.

---

### 2.2 Comms agent (Wave 4)

#### FR-CA-001 — Channel-agnostic chat handler (P0)
**Description.** Generalize `process_chat_message(user_id, text)` to `process_chat_message(user_id, text, channel, thread_id, session_id)`. Reply delivered through outbound agent on the originating channel.

**Files to touch.**
- `src/graphclaw/agent/main_orchestrator.py:616` (`process_chat_message`) — extend signature
- `src/graphclaw/agent/main_orchestrator.py:754` (`process_chat_message_stream`) — extend signature
- `src/graphclaw/api/chat.py` — pass `channel="cockpit"`, derive `thread_id`
- `src/graphclaw/inbound/processor.py` — when route=`user_chat`, call comms agent with channel/thread context
- `src/graphclaw/agent/outbound.py` — provide `dispatch_reply(thread_context, text)` for in-thread responses

**Acceptance.**
- AC1: Cockpit chat works unchanged.
- AC2: Telegram message from owner triggers comms agent and reply lands on Telegram via outbound.
- AC3: Same `thread_id` preserved across the round-trip.

**Dependencies.** FR-IN-001, FR-OUT-001, FR-STORE-001.

---

#### FR-CA-002 — Post-turn distillation (P0)
**Description.** After every chat turn (cockpit or any channel), run InboundIntelligenceAgent-style distillation: extract `task_entry` → `node.intelligence`; `memory_note` → `working/context.md`.

**Files to touch.**
- `src/graphclaw/agent/main_orchestrator.py:616` — append distillation post-step after agentic loop returns
- `src/graphclaw/agent/distillation.py` — **NEW**: shared distillation helper used by both inbound and comms paths
- `src/graphclaw/inbound/intelligence_agent.py` — refactor distillation into shared helper

**Implementation notes.**
- Distillation runs through outbox (FR-RES-001) for retry idempotency.
- LLM call for extraction may share the chat turn or be a small follow-up call (stays small via system prompt + last 3 messages).

**Acceptance.**
- AC1: Cockpit chat referencing task TSK-X writes intelligence line on TSK-X.
- AC2: Cockpit chat with cross-task observation writes memory_note to `working/context.md`.
- AC3: Distillation failure does NOT block reply to user (writes go through outbox).

**Dependencies.** FR-RES-001 (distillation outbox), FR-CA-001.

---

#### FR-CA-003 — counterparty_conversation mode (P0)
**Description.** New mode the comms agent enters when wakened by inbound for a counterparty (not the owner). System prompt variant constrains tools and behavior; loads delegation/etiquette/tone policies.

**Files to touch.**
- `src/graphclaw/agent/main_orchestrator.py` — `process_counterparty_turn(user_id, counterparty_id, text, channel, thread_id, session_id)` new entry point
- `src/graphclaw/gateway/prompts/system_header_counterparty.md` — **NEW** prompt variant
- `src/graphclaw/agent/main_orchestrator.py:1101` (`_build_system_prompt`) — accept `mode` param; inject policy bodies when mode=`counterparty_conversation`
- `src/graphclaw/agent/tool_registry.py` — `get_active_tools(mode)` filters tool set per mode

**Tool allow-list in counterparty_conversation mode.**
- Allowed: `get_task_details`, `update_task_state` (gated by delegation policy), `send_message` (same thread/channel only), `update_node_intelligence` (server-side via archive_*), `escalate_to_owner`
- Disabled: `delegate_to_agent`, `create_agent`, `invoke_skill` (unless allow-listed in policy), `call_mcp_tool`

**Acceptance.**
- AC1: Mode switches based on inbound route classification.
- AC2: Disallowed tool calls return `ToolNotAvailableInMode` error.
- AC3: Policy-evaluator gates `update_task_state` per `allowed_state_transitions`.

**Dependencies.** FR-STORE-002, FR-IN-002.

---

### 2.3 Inbound agent (Wave 3)

#### FR-IN-001 — Sender classification + routing (P0)
**Description.** Add classification step at top of `InboundProcessor.process` returning one of: `user_chat`, `counterparty_reply`, `counterparty_proactive`, `unknown_party`, `drop`.

**Files to touch.**
- `src/graphclaw/inbound/processor.py:91` — insert classification step
- `src/graphclaw/inbound/router.py` — **NEW**: `RouteDecision`, classification logic
- `src/graphclaw/inbound/intelligence_agent.py` — branch on route
- `src/graphclaw/gateway/agent_channel_identity.py` — **NEW**: receiving-account → `(user_id, agent_id)` lookup (FR-IN-003)

**Routing matrix.**
| Sender match | Reply-key match | Receiving account → owner | Route |
|---|---|---|---|
| Owner's own identity | n/a | yes | `user_chat` |
| Known counterparty | yes | yes | `counterparty_reply` |
| Known counterparty | no | yes | `counterparty_proactive` |
| Unknown sender | n/a | yes | `unknown_party` |
| Any | n/a | no | `drop` |

**Acceptance.**
- AC1: Owner's own Telegram message routes to `user_chat` and triggers comms agent loop.
- AC2: Counterparty reply on existing thread routes to `counterparty_reply`.
- AC3: Unknown phone number routes to `unknown_party` (notification to owner).

**Dependencies.** FR-IN-003, FR-GRAPH-001.

---

#### FR-IN-002 — Counterparty resolution (P0)
**Description.** Resolve `(channel, sender_external_id)` → `ResourceNode | UserNode` scoped to the owner's substrate + org membership.

**Files to touch.**
- `src/graphclaw/gateway/alias_resolver.py` — extend with `resolve_to_node(channel, sender_id, owner_user_id)` that consults Redis AliasResolver THEN UserNode/ResourceNode `identities` field
- `src/graphclaw/inbound/router.py` — call resolver

**Acceptance.**
- AC1: Bob's known telegram_id resolves to Bob's ResourceNode shadow in User-1's substrate.
- AC2: Unknown sender returns None.

**Dependencies.** FR-GRAPH-001, FR-GRAPH-003.

---

#### FR-IN-003 — AgentChannelIdentity registry (P0)
**Description.** Map receiving accounts (Telegram bot id, email mailbox, WhatsApp number) → `(user_id, agent_id)`.

**Files to touch.**
- `src/graphclaw/models/agent_channel_identity.py` — **NEW**: model
- `src/graphclaw/migrations/0XX_agent_channel_identity.py`
- `src/graphclaw/gateway/agent_channel_identity.py` — service: load on startup, cache in memory, hot-reload on admin update
- `src/graphclaw/api/admin/agent_channels.py` — **NEW**: CRUD endpoints
- `cockpit/src/features/admin/AgentChannelsPanel.tsx` — **NEW**

**Schema.**
```python
class AgentChannelIdentity(BaseModel):
    user_id: str
    agent_id: str
    channel: str  # "telegram"|"email"|"whatsapp"|...
    account_id: str  # bot username, mailbox, phone number
    display_name: str
    credentials_ref: str  # pointer to secret
    active: bool = True
```

**MVP fallbacks** for shared bot/email: deep-link `start` parameter for Telegram (`tg://start?user_id=USR-1`), `+alias` for email (`agent+user1@…`).

**Acceptance.**
- AC1: Admin can create/edit/delete entries.
- AC2: Inbound classifier uses entries to resolve owner.
- AC3: Disabled entry rejects inbound with `drop` route.

**Dependencies.** None.

---

### 2.4 Outbound agent (Wave 2)

#### FR-OUT-001 — Outbound peer agent loop (P0)
**Description.** Promote `OutboundDispatcher` to `OutboundCommunicationAgent` — system prompt + per-user profile + LLM-driven loop for drafting/refining + channel resolution + batching + CheckinNode + intelligence write.

**Files to touch.**
- `src/graphclaw/agent/outbound.py` — refactor `OutboundDispatcher` → `OutboundCommunicationAgent`
- `src/graphclaw/gateway/prompts/outbound_header.md` — **NEW**
- `src/graphclaw/infra/storage.py` — `StoragePaths.outbound_profile(user_id, agent_id)`
- Per-user `{user_id}/agents/{user_id}/outbound_profile.md` (created at onboarding)
- `src/graphclaw/agent/outbound_intent.py` — **NEW**: `OutboundIntent { task_id, recipient_id, purpose, draft? }`

**Acceptance.**
- AC1: Comms agent calls `outbound.send(OutboundIntent(...))`; outbound resolves recipient + channel and dispatches.
- AC2: Outbound's policy-evaluator runs (FR-OUT-003) before dispatch.

**Dependencies.** FR-STORE-002, FR-OUT-002.

---

#### FR-OUT-002 — Channel resolution from preferences (P0)
**Description.** Resolve channel from `UserNode.preferences.preferred_channel` OR `ResourceNode.communication_preferences.preferred_channel` (read-through if linked). Honor channel-stickiness window.

**Files to touch.**
- `src/graphclaw/agent/outbound.py` — `_resolve_channel(recipient_id, override?)` method
- `src/graphclaw/db/age/repository.py` — `get_active_thread(recipient_id, channel)` for stickiness check

**Logic.**
```
1. If override channel given → use it.
2. Else: get recipient (read-through linked_user_id if shadow).
3. preferred = recipient.preferences.preferred_channel (or default email).
4. If active CheckinNode thread exists on a different channel within
   channel_stickiness_hours window → use that channel instead.
5. Dispatch via channel adapter.
```

**Acceptance.**
- AC1: Bob's preference telegram → outbound dispatches via Telegram.
- AC2: Active email thread <48h old + Bob just changed pref to whatsapp → outbound stays on email.

**Dependencies.** FR-GRAPH-005, FR-GRAPH-003.

---

#### FR-OUT-003 — Delegation policy enforcement at outbound (P0)
**Description.** Outbound enforces delegation policy regardless of caller (comms agent, sub-agent, skill, scheduler).

**Files to touch.**
- `src/graphclaw/agent/outbound.py` — call policy evaluator at entry; reject if intent violates hard limits
- `src/graphclaw/agent/policies/evaluator.py` — `evaluate_outbound_intent(intent, policy) -> AllowOrEscalate`

**Acceptance.**
- AC1: Skill calling outbound for a counterparty action that exceeds policy → escalate path.
- AC2: Comms agent in counterparty mode hits same enforcement.

**Dependencies.** FR-STORE-002, FR-OUT-001.

---

#### FR-OUT-004 — CheckinNode + Redis reply-key + intelligence write (P0)
**Description.** Every dispatch creates a CheckinNode with new fields (FR-GRAPH-004), sets Redis reply-key with 7d TTL, AND a persistent `(channel, thread_id) → {task, counterparty}` row (FR-RES-002), AND appends to `node.intelligence`.

**Files to touch.**
- `src/graphclaw/agent/outbound.py` — post-dispatch hook
- `src/graphclaw/inbound/reply_keys.py` — **NEW**: dual write to Redis + Postgres `reply_lineage`

**Acceptance.**
- AC1: After Telegram send, Redis has `checkin:telegram:{thread}:{msg}` and Postgres `reply_lineage` row.
- AC2: `node.intelligence` for the task has new outbound line.

**Dependencies.** FR-GRAPH-004, FR-RES-002.

---

### 2.5 Per-user policies (Wave 4)

#### FR-POL-001 — Policy file substrate (P0)
**Description.** All per-user policies live as MinIO `.md` files at `{user_id}/agents/{agent_id}/policies/*.md`. Loaded into agent system prompt at turn time, Redis-cached 15min, parsed YAML frontmatter for hard limits.

**Files to touch.** See FR-STORE-002.

**Policies and defaults.**
| File | fail_mode default | Purpose |
|---|---|---|
| `delegation.md` | closed | What agent may do unsupervised on owner's behalf |
| `escalation.md` | closed | When to interrupt the owner |
| `counterparty_etiquette.md` | degraded | Tone/conventions for counterparty-facing comms |
| `reply_tone.md` | degraded | Voice for outbound drafting |

**Acceptance.** See FR-STORE-002 ACs.

**Dependencies.** FR-STORE-002.

---

#### FR-POL-002 — Intelligence Hub policies editor (P1)
**Description.** Cockpit editor for policy files: structured form on YAML frontmatter + markdown body editor.

**Files to touch.**
- `cockpit/src/features/intelligence/PoliciesPanel.tsx` — **NEW**
- `cockpit/src/features/intelligence/PolicyEditor.tsx` — **NEW** (form + markdown editor)
- `cockpit/docs/prd/15-intelligence-hub.md` — add Policies left-nav item

**Acceptance.**
- AC1: User edits delegation.md via form; frontmatter validated client-side and server-side.
- AC2: Body markdown rendered with preview.
- AC3: Save triggers Redis cache invalidation.

**Dependencies.** FR-POL-001.

---

### 2.6 Identity, onboarding, resolution (Wave 7)

#### FR-ID-001 — Onboarding FSM in main orchestrator (P0)
**Description.** First-run experience: `WELCOME → PERSONA → CHANNELS → WORKING_HOURS → PREFERENCES → POLICIES → DONE`. State persisted in profile.md frontmatter; resumable.

**Files to touch.**
- `src/graphclaw/agent/onboarding.py` — **NEW**: state machine + per-state prompt variants + per-state tool allow-lists
- `src/graphclaw/agent/main_orchestrator.py` — detect `profile.md` missing or `onboarding_complete: false` → route to onboarding
- `src/graphclaw/gateway/prompts/onboarding/{welcome,persona,channels,working_hours,preferences,policies}.md` — **NEW** per-state system prompts
- `src/graphclaw/agent/tools/onboarding_tools.py` — **NEW**: `set_user_name`, `set_user_persona`, `add_user_identity`, `set_working_hours`, `set_preferences`, `seed_policy_from_template`, `complete_onboarding`

**Acceptance.**
- AC1: First-time user's first chat triggers WELCOME.
- AC2: Quitting mid-PERSONA and returning resumes from PERSONA.
- AC3: DONE writes `onboarding_complete: true` to profile.md frontmatter.
- AC4: Returning user with no frontmatter defaults to `onboarding_complete: true` (migration).

**Dependencies.** FR-STORE-002, FR-GRAPH-001.

---

#### FR-ID-002 — resolve_user(query, hints?) tool (P0)
**Description.** Returns ranked candidates from local + org directory with confidence + source + reason. Scoped to orgs the caller is a member of.

**Files to touch.**
- `src/graphclaw/agent/tools/identity_tools.py` — **NEW**: `resolve_user`, `register_alias`, `merge_resource`
- `src/graphclaw/identity/resolver.py` — **NEW**: implementation
- `src/graphclaw/agent/tool_registry.py` — register in core tools

**Output schema.**
```python
class ResolutionCandidate(BaseModel):
    node_id: str
    source: Literal["local", "org_directory"]
    confidence: float
    reason: str
    display_name: str
    discriminators: dict  # role, workspace, email_domain
```

**Algorithm.** See `arch/15-user-identity-and-onboarding.md` §Resolution.

**Acceptance.**
- AC1: Local exact alias hit returns confidence 1.0.
- AC2: Org directory match returns source="org_directory".
- AC3: Multiple candidates returned for ambiguous query.

**Dependencies.** FR-GRAPH-002, FR-DIR-001.

---

#### FR-ID-003 — create_person_via_dialog FSM with disambiguation (P0)
**Description.** When resolution fails, walk user through field collection. **First state offers top-N existing local candidates** before falling to "new external person" — closes the Mr. Smith / Bob duplication gap.

**Files to touch.**
- `src/graphclaw/agent/identity/create_person.py` — **NEW**: FSM (DISAMBIGUATE → NAME → ROLE → CHANNEL → CONTACT → ALIASES → DONE)
- `src/graphclaw/agent/tools/identity_tools.py` — `start_create_person_dialog`

**Acceptance.**
- AC1: When User-1 says "delegate to Bob" and Mr. Smith exists, DISAMBIGUATE state offers Mr. Smith first.
- AC2: User can pick existing → alias-drift autoload (FR-ID-005).
- AC3: User can pick "new" → continues NAME → … → DONE.

**Dependencies.** FR-ID-002.

---

#### FR-ID-004 — merge_resource tool (P0)
**Description.** Post-hoc deduplication: redirect edges, concatenate aliases + intelligence + conversations chronologically, archive merged-into node with tombstone redirect.

**Files to touch.**
- `src/graphclaw/agent/tools/identity_tools.py` — `merge_resource(keep_id, merge_id, canonical_name?)`
- `src/graphclaw/identity/merger.py` — **NEW**: implementation (uses admin-side helper to write tombstone since lifecycle fields are admin-only)
- `src/graphclaw/db/age/repository.py` — `redirect_edges(from_id, to_id)` admin-side helper

**Acceptance.**
- AC1: After merge, `resolve_canonical(merge_id)` returns `keep_id`.
- AC2: keep_id has merged aliases (deduplicated) and chronologically-sorted intelligence.
- AC3: Conversations merged per FR-RES-005.
- AC4: Active comms-agent sessions on either node receive cache-invalidation event.

**Dependencies.** FR-DEL-002, FR-DEL-003, FR-GRAPH-002, FR-RES-005.

---

#### FR-ID-005 — Alias-drift autoload (P1)
**Description.** When fuzzy-match resolution succeeds with an alias not yet on the node, append to `aliases` with provenance.

**Files to touch.**
- `src/graphclaw/identity/resolver.py` — post-resolution hook
- `src/graphclaw/agent/tools/identity_tools.py` — `register_alias` (server-side helper)

**Acceptance.**
- AC1: User says "Bob" matching `RES-mrsmith-001` via fuzzy → `aliases` gets `{value: "Bob", added_at, added_by, source: "auto-fuzzy"}`.

**Dependencies.** FR-GRAPH-002.

---

### 2.7 Org directory (Wave 8)

#### FR-DIR-001 — Org-scoped Postgres user directory (P0)
**Description.** Per-org index of UserNodes for fast fuzzy + identity search. Updated when UserNode or OrganizationNode.members changes.

**Files to touch.**
- `src/graphclaw/identity/directory.py` — **NEW**: read API
- `src/graphclaw/identity/directory_indexer.py` — **NEW**: event-bus consumer
- `src/graphclaw/migrations/0XX_user_directory.py` — **NEW**: Postgres table + trigram index
- `src/graphclaw/identity/embedding.py` — embedding column for semantic match

**Schema.**
```sql
CREATE TABLE user_directory (
  user_id TEXT,
  org_id TEXT,
  display_name TEXT,
  emails TEXT[],
  identities JSONB,
  discoverable_aliases TEXT[],
  visibility_policy TEXT,
  last_updated TIMESTAMPTZ,
  PRIMARY KEY (user_id, org_id)
);
CREATE INDEX user_directory_name_trgm ON user_directory USING gin (display_name gin_trgm_ops);
CREATE INDEX user_directory_aliases_trgm ON user_directory USING gin (discoverable_aliases);
```

**Acceptance.**
- AC1: Profile change emits event; indexer updates row.
- AC2: Member added to org → row appears in `(user_id, org_id)` pair.
- AC3: Query "Bob" returns matches scoped to caller's orgs.

**Dependencies.** FR-GRAPH-006, FR-AK (cascade).

---

#### FR-DIR-002 — Cross-org membership scoping (P0)
**Description.** SaaS rule: `resolve_user` only returns candidates in orgs the caller is also a member of.

**Files to touch.**
- `src/graphclaw/identity/resolver.py` — fetch caller's `OrganizationNode.members` memberships; intersect
- `src/graphclaw/identity/directory.py` — query takes `caller_org_ids: list[str]`

**Acceptance.**
- AC1: Carol in ORG-A and ORG-B; User-1 only in ORG-A → Carol resolves only via ORG-A row.

**Dependencies.** FR-DIR-001.

---

### 2.8 Cross-tenant task projection — A.1 (Wave 8.5)

#### FR-XT-001 — Org task index (P0)
**Description.** Postgres index per-org of tasks with their assignees' linked_user_ids. Used by `list_external_assignments_for_me`.

**Files to touch.**
- `src/graphclaw/cross_tenant/task_index.py` — **NEW**
- `src/graphclaw/cross_tenant/indexer.py` — **NEW**: event-bus consumer
- `src/graphclaw/migrations/0XX_org_task_index.py`

**Schema.**
```sql
CREATE TABLE org_task_index (
  task_id TEXT PRIMARY KEY,
  owner_user_id TEXT,
  org_id TEXT,
  workspace_id TEXT,
  assignee_linked_user_ids TEXT[],
  state TEXT,
  deadline TIMESTAMPTZ,
  last_activity_at TIMESTAMPTZ,
  summary_text TEXT,
  archived_at TIMESTAMPTZ
);
CREATE INDEX org_task_index_assignees ON org_task_index USING gin (assignee_linked_user_ids);
CREATE INDEX org_task_index_org_state ON org_task_index (org_id, state) WHERE archived_at IS NULL;
```

**Acceptance.**
- AC1: Task create/update/state-transition events update index within 5s.
- AC2: `archived_at` propagated; queries filter by default.

**Dependencies.** FR-GRAPH-003.

---

#### FR-XT-002 — list_external_assignments_for_me + get_external_task_summary (P0)
**Description.** Tools available to every comms agent for assignee-side visibility.

**Files to touch.**
- `src/graphclaw/agent/tools/external_assignments.py` — **NEW**
- `src/graphclaw/cross_tenant/repo.py` — **NEW**: queries with mandatory ACL filter

**Acceptance.**
- AC1: Brian's call returns tasks owned by others where Bob is in `assignee_linked_user_ids`, scoped to Bob's orgs.
- AC2: Summary returns redacted view (title, deadline, owner display, state, last activity); full body NOT returned.

**Dependencies.** FR-XT-001, FR-AL.

---

#### FR-XT-003 — Cross-tenant ACL at repository layer (P0)
**Description.** All cross-tenant queries enforce `org_id IN caller.orgs AND (caller.user_id == owner OR caller.user_id IN assignee.linked_user_ids)` at the repo/query-builder layer; cannot be bypassed by application code.

**Files to touch.**
- `src/graphclaw/cross_tenant/repo.py` — query builder enforces filter
- `src/graphclaw/cross_tenant/acl.py` — **NEW**: ACL helpers + tests
- `src/graphclaw/api/deps.py` — inject `caller_context` (user_id, org_ids) into repo

**Acceptance.**
- AC1: Test-attempt to query without filter raises `ACLViolation`.
- AC2: Cross-org query for non-member org returns empty.

**Dependencies.** FR-XT-001, FR-AL.

---

#### FR-XT-004 — Assignee-side briefing extension (P1)
**Description.** Brian's daily briefing aggregator unions local tasks + `list_external_assignments_for_me`. External tasks visually distinguished.

**Files to touch.**
- `src/graphclaw/agent/briefing.py` — extend
- `src/graphclaw/triggers/briefing.py` — pass cross-tenant context
- `cockpit/src/features/briefing/BriefingView.tsx` — render external section

**Acceptance.**
- AC1: Briefing shows separate "Assigned by others" section.

**Dependencies.** FR-XT-002.

---

#### FR-XT-005 — Org-scoped assignment validation (P0)
**Description.** Refuse `linked_user_id` whose UserNode isn't in the task's org. Surface user-friendly error.

**Files to touch.**
- `src/graphclaw/agent/tools/task_management.py` — `update_task` validates assignee.linked_user_id ∈ task.org.members
- `src/graphclaw/state/machine.py` — same check on state transitions

**Acceptance.**
- AC1: Assigning Bob (ORG-A only) to TSK-X (ORG-C) raises `AssigneeNotInOrg` with "invite Bob to ORG-C" suggestion.

**Dependencies.** FR-GRAPH-003, FR-DIR-002.

---

### 2.9 Scheduler / follow-ups (Wave 5)

#### FR-SCHED-001 — FollowUpTrigger (P0)
**Description.** Cron-driven trigger that selects follow-up candidates and invokes the comms agent in `trigger=follow_up_review` mode.

**Files to touch.**
- `src/graphclaw/triggers/follow_up.py` — **NEW**
- `src/graphclaw/agent/main_orchestrator.py` — accept `trigger` payload as a synthetic system message
- `src/graphclaw/api/admin/triggers.py` — admin tuning endpoint

**Candidate query (per user).**
```sql
SELECT task_id FROM tasks
WHERE owner_user_id = :user_id
  AND state IN ('WAITING','IN_PROGRESS')
  AND archived_at IS NULL
  AND (now() - last_outbound_at) >= :follow_up_days
  AND interrupt_threshold_ok
```

**Acceptance.**
- AC1: Cron tick triggers candidate query and invokes comms agent.
- AC2: Comms agent's distillation post-step runs identically to chat path.
- AC3: Outbound dispatches per FR-OUT-*.

**Dependencies.** FR-CA-002, FR-OUT-001.

---

#### FR-SCHED-002 — Owner-offline escalation queue (P1)
**Description.** Pending-decision queue with timeout fallback when owner unreachable.

**Files to touch.**
- `src/graphclaw/agent/escalation.py` — **NEW**
- `src/graphclaw/migrations/0XX_escalation_queue.py`
- `cockpit/src/features/cockpit/PendingDecisionsBanner.tsx` — **NEW**

**Schema.**
```sql
CREATE TABLE escalation_queue (
  id UUID PRIMARY KEY,
  user_id TEXT,
  context_ref TEXT,
  prompt TEXT,
  proposed_action JSONB,
  created_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  resolution TEXT
);
```

**Acceptance.**
- AC1: Pending decision visible in cockpit on next session.
- AC2: Per-policy `on_owner_unreachable_after_hours` fallback triggers conservative action.

**Dependencies.** FR-CA-003, FR-POL-001.

---

### 2.10 Briefing rendering (Wave 7)

#### FR-BRF-001 — Entity-grouped briefing (P0)
**Description.** Briefing groups task lists by `assignee.node_id` (not display name); renders canonical name + parenthetical aliases when >1 alias used in window.

**Files to touch.**
- `src/graphclaw/agent/briefing.py` — refactor aggregation
- `src/graphclaw/agent/briefing_renderer.py` — **NEW**

**Acceptance.**
- AC1: Bob (alias of Mr. Smith) shows as "Bob (also: Mr. Smith) — TSK-Y, TSK-Z".

**Dependencies.** FR-GRAPH-002.

---

#### FR-BRF-002 — Duplicate-suspicion pass (P1)
**Description.** Briefing fuzzy-matches recently-touched ResourceNodes; surfaces "possible duplicates — merge?" prompts.

**Files to touch.**
- `src/graphclaw/agent/briefing.py` — duplicate-suspicion step

**Acceptance.**
- AC1: Two similarly-named active resources → briefing surfaces merge prompt.

**Dependencies.** FR-ID-004.

---

### 2.11 Resilience (Wave 10)

#### FR-RES-001 — Distillation outbox (P0)
**Description.** Distillation writes go through outbox table with idempotency key per `(message_id, target)`; retried until success.

**Files to touch.**
- `src/graphclaw/distillation/outbox.py` — **NEW**
- `src/graphclaw/migrations/0XX_distillation_outbox.py`
- `src/graphclaw/workers/distillation_worker.py` — **NEW**

**Acceptance.**
- AC1: Failed write retried; idempotency prevents duplicate intelligence lines.

**Dependencies.** FR-CA-002.

---

#### FR-RES-002 — Persistent reply lineage (P0)
**Description.** Postgres `reply_lineage` table for `(channel, thread_id) → {task, counterparty}` lookup with no TTL. Redis stays as fast path; Postgres is long-tail safety net.

**Files to touch.**
- `src/graphclaw/inbound/reply_lineage.py` — **NEW**
- `src/graphclaw/migrations/0XX_reply_lineage.py`

**Acceptance.**
- AC1: Reply on day 8 (after Redis TTL) resolves via Postgres.
- AC2: Cross-channel reply matches by content fingerprint as fallback.

**Dependencies.** FR-OUT-004.

---

#### FR-RES-003 — Per-file write locks for shared MinIO state (P1)
**Description.** Extend `_memory_lock` pattern to `conversations/*.jsonl`, `profile.md`, `policies/*.md`. Or move to versioned-write with optimistic concurrency.

**Files to touch.**
- `src/graphclaw/infra/storage_locks.py` — **NEW**
- `src/graphclaw/inbound/intelligence_agent.py` — use locks
- `src/graphclaw/agent/outbound.py` — use locks

**Acceptance.**
- AC1: Concurrent writes preserve append-order.

**Dependencies.** FR-STORE-001.

---

#### FR-RES-004 — Channel-identity drift recovery (P1)
**Description.** Fuzzy fallback when AliasResolver misses; owner-confirmed re-binding.

**Files to touch.**
- `src/graphclaw/inbound/router.py` — fallback path on `unknown_party` route
- `src/graphclaw/inbound/identity_drift.py` — **NEW**

**Acceptance.**
- AC1: Bob from new Telegram handle classified `unknown_party` → fallback by display name proposes Bob → owner confirms → AliasResolver updated.

**Dependencies.** FR-IN-001, FR-IN-002.

---

#### FR-RES-005 — Conversation-path redirect on merge (P0)
**Description.** When `merge_resource` archives a counterparty shadow, append-merge-sort `.jsonl` files into keep-id's same-channel paths; write `.tombstone` redirect at the old path.

**Files to touch.**
- `src/graphclaw/identity/merger.py` — convo-path merge logic
- `src/graphclaw/infra/storage.py` — `merge_jsonl_chronological` helper

**Acceptance.**
- AC1: After merge, all entries readable under keep-id's path in chronological order.
- AC2: Old path returns redirect.

**Dependencies.** FR-ID-004.

---

### 2.12 Cascade & ACL (Wave 8)

#### FR-AK-001 — Membership-change cascade (P0)
**Description.** When `OrganizationNode.members` changes, fan-out to org-task-index ACL, user-directory, ResourceNode shadow `link_status`.

**Files to touch.**
- `src/graphclaw/cascade/membership.py` — **NEW**
- `src/graphclaw/agent/event_consumer.py` — subscribe to membership events

**Acceptance.**
- AC1: Removing Bob from ORG-X → directory rows for Bob in ORG-X archived; org_task_index entries adjusted; existing shadows flip `link_status` to `detached_org_left`.

**Dependencies.** FR-DIR-001, FR-XT-001, FR-GRAPH-003.

---

#### FR-AL-001 — Mandatory org-scoped ACL at repo layer (P0)
**Description.** Query builder enforces tenant filter; cannot be bypassed by application code. Mirrors MinIO `{user_id}/` partitioning pattern.

**Files to touch.**
- `src/graphclaw/db/age/repository.py` — query builder requires `caller_context`
- `src/graphclaw/cross_tenant/acl.py` — central ACL module
- All API routes — inject `caller_context` via FastAPI dependency

**Acceptance.**
- AC1: Repo call without `caller_context` raises `ACLContextMissing`.
- AC2: Cross-org leak attempts unit-tested per scenario in §9.8.12.

**Dependencies.** None — pairs with FR-XT-003.

---

### 2.13 Multi-agent admin (Wave 9)

#### FR-AM-001 — Multi-agent admin UI (P1)
**Description.** Cockpit Settings panel for users with >1 agent. Create/rename/delete (archive) additional agents per user.

**Files to touch.**
- `cockpit/src/features/settings/MultiAgentPanel.tsx` — **NEW**
- `src/graphclaw/api/admin/agents.py` — **NEW** REST endpoints

**Acceptance.**
- AC1: User-1 creates Felicia (work agent) alongside Angela (personal); each gets own profile, memory, policies, AgentChannelIdentity bindings.

**Dependencies.** FR-IN-003, FR-STORE-002.

---

### 2.14 Counterparty detachment (Wave 8.5)

#### FR-AD-001 — Counterparty detachment / freeze (P0)
**Description.** When `linked_user_id` becomes unreachable (member archived, account purged), shadow freezes last-known canonical data, sets `link_status`, surfaces a one-time prompt.

**Files to touch.**
- `src/graphclaw/cascade/detachment.py` — **NEW**
- `src/graphclaw/agent/event_consumer.py` — subscribe to user archive events

**Acceptance.**
- AC1: Bob archived → User-1's shadow gets `link_status=detached_user_archived`, last-known identities frozen on shadow.
- AC2: Cockpit shows banner: "Bob's account was removed — keep contact info, or unassign?"

**Dependencies.** FR-GRAPH-003, FR-DEL-003.

---

#### FR-AE-001 — Org-task-index reconciliation (P1)
**Description.** Nightly full-sync diff vs AGE truth + admin rebuild endpoint.

**Files to touch.**
- `src/graphclaw/cross_tenant/reconciler.py` — **NEW**
- `src/graphclaw/api/admin/reconciliation.py` — `POST /admin/cross-tenant/rebuild`

**Acceptance.**
- AC1: Drift detected and corrected with audit entry.

**Dependencies.** FR-XT-001.

---

### 2.15 Documentation & cockpit UI (Wave 9)

#### FR-UI-001 — Cockpit conversation views (P0)
**Description.** Surface counterparty conversations in task views, distinguished from owner-self chats.

**Files to touch.**
- `cockpit/src/features/tasks/CounterpartyConversations.tsx` — **NEW**
- `cockpit/src/features/chat/ChannelTaggedHistory.tsx` — render channel tags
- `cockpit/docs/prd/12-task-views.md` — update spec
- `cockpit/docs/prd/13-chat-interface.md` — update spec

**Acceptance.**
- AC1: TSK-X detail shows owner-discussion section AND counterparty-thread sections (one per counterparty).

**Dependencies.** FR-STORE-001.

---

#### FR-UI-002 — SaaS org switcher (P1)
**Description.** Top-level org switcher for users in multiple orgs (SaaS deployment).

**Files to touch.**
- `cockpit/src/features/auth/OrgSwitcher.tsx` — **NEW**
- `cockpit/docs/prd/02-graph-cockpit.md` — update header spec

**Acceptance.**
- AC1: User in ORG-A and ORG-B switches context; views/tools filter to selected org.

**Dependencies.** FR-DIR-001, FR-DIR-002.

---

## 3. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-001 | Distillation post-step adds ≤1.5s p95 to chat-turn latency |
| NFR-002 | `resolve_user` p95 < 200ms for org with 10k members |
| NFR-003 | Org-task-index lag ≤5s p95 from event emission to query visibility |
| NFR-004 | Cross-tenant query MUST NOT return data from non-shared orgs (zero-tolerance privacy guarantee) |
| NFR-005 | Anti-delete probe MUST run on every deploy and on every nightly schedule |
| NFR-006 | Purge worker heartbeat alerting MUST page within 2× expected interval of silence |
| NFR-007 | Audit log entries MUST be immutable (append-only) and survive 7y retention regardless of substrate purges |
| NFR-008 | All agent DB connections MUST log `principal_name` |
| NFR-009 | Policy file load MUST fall back per `fail_mode` setting; no global hard-fail |

---

## 4. Build plan / wave breakdown

> Waves are sequenced; wave N+1 depends on wave N landing. Within a wave, FRs may parallelize.

### Wave 0 — Foundational: No-Delete principle (BLOCKS ALL OTHERS)
**Goals**: service-principal split + semantic-delete prevention + archive primitives + infrastructure guards.
**FRs**: FR-DEL-001, FR-DEL-002, FR-DEL-003, FR-DEL-008, FR-AL-001 (foundational pieces). Plus tombstone resolver (FR-DEL-003 includes).
**Critical files**: see each FR.
**Exit criteria**: anti-delete probe green; agent_principal cannot delete or set lifecycle fields anywhere.

### Wave 0.5 — GDPR & lifecycle UX
**FRs**: FR-DEL-004, FR-DEL-005, FR-DEL-006, FR-DEL-007, FR-DEL-009.

### Wave 1 — Schema migrations
**FRs**: FR-GRAPH-001..006, FR-STORE-001, FR-STORE-002.

### Wave 2 — Outbound peer agent
**FRs**: FR-OUT-001, FR-OUT-002, FR-OUT-003, FR-OUT-004.

### Wave 3 — Inbound classification + AgentChannelIdentity
**FRs**: FR-IN-001, FR-IN-002, FR-IN-003.

### Wave 4 — Comms agent extensions
**FRs**: FR-CA-001, FR-CA-002, FR-CA-003, FR-POL-001 (plus FR-POL-002 cockpit UI piece in Wave 9).

### Wave 5 — Scheduler / follow-ups
**FRs**: FR-SCHED-001, FR-SCHED-002.

### Wave 6 — Channel coverage
**FRs (existing scope)**: WhatsApp / Telegram inbound pollers wired to gateway lifespan; per-user bot identity setup.

### Wave 7 — Identity & onboarding
**FRs**: FR-ID-001, FR-ID-002, FR-ID-003, FR-ID-004, FR-ID-005, FR-BRF-001, FR-BRF-002, FR-RES-003, FR-RES-004.

### Wave 8 — Org directory + tenancy + cascade
**FRs**: FR-DIR-001, FR-DIR-002, FR-AK-001, FR-AL-001 (full enforcement).

### Wave 8.5 — Cross-tenant task projection
**FRs**: FR-XT-001..005, FR-AD-001, FR-AE-001.

### Wave 9 — Cockpit UI
**FRs**: FR-POL-002, FR-UI-001, FR-UI-002, FR-AM-001, plus admin surfaces for AgentChannelIdentity, directory visibility, multi-agent.

### Wave 10 — Resilience
**FRs**: FR-RES-001, FR-RES-002, FR-RES-005, plus distillation idempotency, channel stickiness, full-substrate locks.

---

## 5. Verification matrix (FR → test)

Each FR's acceptance criteria → at least one automated test. See companion arch docs for detailed test scenarios. Plan §9.8.12 (functional) and §9.8.17 (No-Delete) walkthroughs map to integration tests.

---

## 6. Migration plan

1. **Wave 0 prerequisites**: Postgres + AGE + MinIO principal grants; backfill `archived_at IS NULL` on existing rows.
2. **Wave 1 chat-history migration**: `scripts/migrate_chat_history.py` runs once per existing user; original `chat/history.json` archived (not deleted).
3. **Wave 1 CheckinNode backfill**: best-effort populate `recipient_id`, `channel`, `thread_id`, `direction` from existing intelligence log lines.
4. **Wave 7 onboarding-state migration**: existing users with no `onboarding_complete` frontmatter default to `true` to avoid re-onboarding.
5. **Wave 8 user-directory backfill**: indexer batch-loads from existing UserNode + OrganizationNode.members.

---

## 7. Open decisions resolved

| OQ | Decision |
|---|---|
| OQ-1 | Archive-tombstone with No-Delete principle (foundational; see §2.0). |
| OQ-2 | `channel_stickiness_hours: int = 48` configurable per user; per-channel overrides. |
| OQ-3 | Per-policy `fail_mode: closed | degraded` in YAML frontmatter; defaults per file. |

---

## 8. Source plan (for audit)

This requirements document distills the design conversation captured in [review-the-design-plans-squishy-eagle.md](review-the-design-plans-squishy-eagle.md) (Sections §1–§9.8.21), which lives alongside this file in `docs/requirements/`. All gaps (A–AX), validation walkthroughs, stress tests, and decisions there map to FRs here.

**Read order for new contributors:**
1. This requirements doc (§1 glossary → §4 wave plan) — the *what* and *when*.
2. The relevant architecture doc (13–19) for the wave being worked — the *how*.
3. [review-the-design-plans-squishy-eagle.md](review-the-design-plans-squishy-eagle.md) — the *why* (gaps, validation walkthroughs, stress tests, design rationale).
