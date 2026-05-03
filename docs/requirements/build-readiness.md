# Build Readiness — Pre-Implementation Checklist

**Status:** Draft v1.0 | **Date:** 2026-05-02 | **Companions:** [agent-triad-and-comms-substrate.md](agent-triad-and-comms-substrate.md), [review-the-design-plans-squishy-eagle.md](review-the-design-plans-squishy-eagle.md)

This document closes the operational gaps between the design (architecture docs 13–19) + the actionable spec (requirements doc) and an actual first-day-of-build. Read this **before** starting Wave 0.

Sections:
1. Wave 0 kickoff checklist
2. Rollout & migration ordering for existing production
3. Verification matrix (FR → test)
4. API contract shapes (new endpoints)
5. Risk register
6. Consolidated Open Questions log
7. Cockpit-side implementation conventions (read first if touching cockpit)

---

## 1. Wave 0 kickoff checklist

Wave 0 (No-Delete principle) is foundational and blocks every other wave. Land these in order:

### 1.1 Pre-conditions
- [ ] Existing `1451 tests passing` baseline confirmed locally and in CI.
- [ ] Database backups taken before applying any DDL migration in this wave.
- [ ] Feature flag `NO_DELETE_ENFORCEMENT_ENABLED` (env: `GRAPHCLAW_NO_DELETE_ENFORCEMENT`) — defaults `false` initially; flipped to `true` only after all Wave 0 PRs merged and probe tests green.
- [ ] Confirm staging env mirrors prod's MinIO bucket policy and Postgres role grants.

### 1.2 PR sequence
| # | Title | FRs | Files (primary) | Notes |
|---|---|---|---|---|
| W0-PR1 | Principal definitions + connection factory | FR-DEL-001 | `auth/principals.py` (new), `db/age/repository.py`, `infra/storage.py`, `db/base.py`, `api/deps.py` | No grant changes yet — wires the abstraction. |
| W0-PR2 | Postgres role grants + MinIO policy | FR-DEL-001, FR-DEL-008 | `infrastructure/postgres/init/grants.sql` (new), `infrastructure/minio/policies/agent-policy.json` (new), `docker-compose.yml` (graphclaw + cockpit) | Apply with feature flag still `false` so existing app code still works via a back-compat alias role until cutover. |
| W0-PR3 | Probe table + startup assertion | FR-DEL-001 | `auth/principals.py`, new migration `0XX_principal_probe.py`, `tests/integration/test_no_delete_probes.py` | Process refuses to start when flag `true` AND probe DELETE succeeds. |
| W0-PR4 | Lifecycle field schema + admin-only triggers | FR-DEL-002, FR-DEL-003 | `models/base.py`, new migration `0XX_lifecycle_fields_and_triggers.py`, [`db/age/repository.py`](../../src/graphclaw/db/age/repository.py) `update_node` filters lifecycle fields when caller is `agent_principal` | Test trigger fires on `archived_at` write attempt. |
| W0-PR5 | TombstoneNode + resolve_canonical | FR-DEL-003 | `models/nodes.py`, new `db/age/redirects.py`, new migration `0XX_tombstone_node.py` | Add cycle test, max-hop test. |
| W0-PR6 | archive_* tools (replace any delete_*) | FR-DEL-002 | new `agent/tools/archive.py`, [`agent/tool_registry.py`](../../src/graphclaw/agent/tool_registry.py), [`state/machine.py`](../../src/graphclaw/state/machine.py) (forbidden states `PURGED`/`DELETED`) | Audit-grep entire repo for `delete_*` tool names; remove or rename. |
| W0-PR7 | Mandatory caller_context at repo layer | FR-AL-001 | [`db/age/repository.py`](../../src/graphclaw/db/age/repository.py), new `cross_tenant/acl.py`, [`api/deps.py`](../../src/graphclaw/api/deps.py) | Repo raises `ACLContextMissing` if caller_context not provided. |
| W0-PR8 | Infrastructure config audit on startup | FR-DEL-008 | new `observability/startup_audit.py`, new `infrastructure/minio/policies/lifecycle-audit.sh` | Process fails to start if any user-prefix lifecycle rule found. |
| W0-PR9 | Anti-delete CI probes (full suite) | (verification) | `tests/integration/test_no_delete_probes.py` (extend) | Every agent + every tool tested. Runs on every PR + nightly. |
| W0-PR10 | Flip the flag | — | env config | After all above land + probes green. |

### 1.3 Wave 0 exit criteria
- All probe tests green in CI for 7 consecutive days.
- Integration test scenarios from plan §9.8.17 (DEL-01..DEL-20) covered by tests.
- No production agent process can execute `DELETE` against any user-data table or `s3:DeleteObject` against any user prefix.
- Lifecycle field updates from agent context raise `InsufficientPrivilege`.

### 1.4 Wave 0.5 follow-on (GDPR & lifecycle UX)
Land Wave 0.5 (FR-DEL-004..009) immediately after Wave 0 to complete the user-facing flows. Until 0.5 lands, "Delete my data" cockpit flow is hidden behind feature flag `LIFECYCLE_UX_ENABLED`.

---

## 2. Rollout & migration ordering for existing production

The existing system has **1451 passing tests, 6 waves complete, ~production-ready data substrate**. Rolling Wave 0 onto live data needs care.

### 2.1 Migration order (one wave-0 release)

```
1. backup database + minio bucket
2. apply migration 0XX_principal_probe (creates probe table)
3. create role agent_principal (no grants yet)
4. create role admin_principal (existing app code keeps working as old role)
5. create role migration_principal
6. apply migration 0XX_lifecycle_fields_and_triggers (adds nullable columns; no defaults backfill yet)
7. apply migration 0XX_tombstone_node
8. deploy app code with feature flag NO_DELETE_ENFORCEMENT_ENABLED=false
   (app still uses old role; new code paths inactive)
9. run anti-delete probe in staging-mirror against prod-shaped data
10. cutover script: backfill archived_at = NULL explicitly on existing rows (NOOP-safe)
11. switch agent processes to use agent_principal (with REVOKE DELETE)
12. switch admin/purge processes to use admin_principal
13. flip NO_DELETE_ENFORCEMENT_ENABLED=true
14. monitor for 24h; rollback path: flip flag false, switch principals back
```

### 2.2 Backfill considerations

- **Existing rows have no `archived_at`.** Migration adds the column nullable with no default. App code interprets `NULL` as "not archived." No mass-update needed.
- **Existing tasks/edges with no `purge_after`** — same treatment.
- **Existing CheckinNodes lack `recipient_id`/`channel`/`thread_id`/`direction`** (FR-GRAPH-004). Backfill is best-effort from intelligence log lines via `scripts/backfill_checkin_fields.py`. Rows that can't be backfilled get `NULL` and are tagged `legacy=true`. Outbound code must tolerate `NULL` on legacy rows.
- **Existing chat history** (`{user_id}/chat/history.json`) — migrated via `scripts/migrate_chat_history.py` (FR-STORE-001) into the new conversations layout with `channel="cockpit"`. Original archived (NOT deleted) under `{user_id}/conversations/.legacy/chat-history.json.archived`.

### 2.3 Rollback playbook

If Wave 0 enforcement causes prod regression:
1. Flip `NO_DELETE_ENFORCEMENT_ENABLED=false`.
2. Switch agent processes back to old role (preserves operations until fix).
3. New schema columns remain (nullable, harmless).
4. Investigate; re-attempt cutover after fix.

Wave 0 is reversible up to step 13. After step 13, rollback requires un-flipping the flag.

---

## 3. Verification matrix (FR → test)

Each FR's acceptance criteria maps to at least one automated test. New tests live alongside the implementation file or under `tests/integration/`.

### 3.1 No-Delete (Wave 0)

| FR | Test file | Key assertions |
|---|---|---|
| FR-DEL-001 | `tests/integration/test_principals.py` | role grants present; probe `DELETE` fails as `agent_principal`; structured log includes `principal_name` |
| FR-DEL-002 | `tests/integration/test_lifecycle_field_protection.py` | UPDATE of `archived_at` from agent connection raises; UPDATE of `state="PURGED"` rejected by state machine |
| FR-DEL-003 | `tests/unit/test_tombstone_resolver.py` | A→B→C resolves to C; cycle detected; max-hop respected |
| FR-DEL-004 | `tests/integration/test_pending_purge_gate.py` | `/auth/login` returns 423 with payload; cancel restores; continue lets timer run |
| FR-DEL-005 | `tests/integration/test_purge_worker.py` | worker deletes past `purge_after`; respects `legal_hold` and `purge_cancelled_at`; heartbeat alerting on stale |
| FR-DEL-006 | `tests/integration/test_right_to_erasure.py` | re-auth required; immutable audit entry written; sync purge completes |
| FR-DEL-007 | `tests/unit/test_legal_hold.py` | hold prevents purge; set/release audit entries |
| FR-DEL-008 | `tests/integration/test_startup_audit.py` | process exits if MinIO lifecycle covers `users/*`; exits if AGE TTL on user labels |
| FR-DEL-009 | `tests/integration/test_org_archive.py` | archiving org leaves member UserNodes intact; workspace archive preserves task readability |

### 3.2 Schema migrations (Wave 1)

| FR | Test file | Key assertions |
|---|---|---|
| FR-GRAPH-001 | `tests/unit/test_node_identities.py` | identities round-trip; AliasResolver write reflects on UserNode |
| FR-GRAPH-002 | `tests/unit/test_node_aliases.py` | alias appended with provenance; deduplication on merge |
| FR-GRAPH-003 | `tests/integration/test_linked_user_id_readthrough.py` | shadow reads through to linked UserNode for prefs/identities; owner-specific stays on shadow |
| FR-GRAPH-004 | `tests/integration/test_checkin_fields.py` | new fields populated by outbound; index lookup performance |
| FR-GRAPH-005 | `tests/unit/test_user_preferences.py` | channel_stickiness defaults; per-channel override |
| FR-GRAPH-006 | `tests/unit/test_org_directory_visibility.py` | settings round-trip; default OPEN |
| FR-STORE-001 | `tests/integration/test_conversation_storage.py` | counterparty-scoped paths; legacy migration; index.json updates |
| FR-STORE-002 | `tests/integration/test_policy_files.py` | YAML+markdown parse; Redis cache; fail_mode honored |

### 3.3 Outbound, inbound, comms (Waves 2–4)

| FR | Test file | Key assertions |
|---|---|---|
| FR-OUT-001..004 | `tests/integration/test_outbound_agent.py` | OutboundIntent flow; channel resolution + stickiness; CheckinNode + reply_lineage dual write; policy enforcement at entry |
| FR-IN-001..003 | `tests/integration/test_inbound_classifier.py` | full routing matrix; AgentChannelIdentity registry hit/miss |
| FR-CA-001..003 | `tests/integration/test_comms_agent_modes.py` | channel-agnostic chat; counterparty mode tool gating; trigger mode |
| FR-POL-001 | `tests/unit/test_policy_evaluator.py` | hard-limit evaluation pre-LLM; `fail_mode` per-policy |

### 3.4 Identity, directory, cross-tenant (Waves 7–8.5)

| FR | Test file | Key assertions |
|---|---|---|
| FR-ID-001 | `tests/integration/test_onboarding_fsm.py` | resumability across states; tool allow-list per state; DONE marker |
| FR-ID-002 | `tests/integration/test_resolve_user.py` | local exact > local fuzzy > org > new-person ordering |
| FR-ID-003 | `tests/integration/test_create_person_dialog.py` | DISAMBIGUATE first; alias-drift autoload on existing-pick |
| FR-ID-004 | `tests/integration/test_merge_resource.py` | edges redirected; aliases concatenated; conversations append-merge-sorted; tombstone redirect; cache invalidation event |
| FR-DIR-001..002 | `tests/integration/test_user_directory.py` | trigram + embedding search; cross-org scoping rule (NFR-004 zero leak) |
| FR-XT-001..005 | `tests/integration/test_cross_tenant.py` | mandatory ACL filter; assignee-side briefing; assignee-not-in-org rejection |
| FR-AK-001 | `tests/integration/test_membership_cascade.py` | member-removed propagates to directory + index + shadows |
| FR-AL-001 | `tests/integration/test_repo_acl_boundary.py` | every repo entry-point fails closed without `caller_context` |

### 3.5 Resilience (Wave 10)

| FR | Test file | Key assertions |
|---|---|---|
| FR-RES-001 | `tests/integration/test_distillation_outbox.py` | partial failure retried; idempotency prevents duplicate intelligence lines |
| FR-RES-002 | `tests/integration/test_reply_lineage.py` | reply 8-day-old resolves via Postgres; cross-channel content fingerprint |
| FR-RES-003 | `tests/integration/test_storage_locks.py` | concurrent writes preserve append order |
| FR-RES-004 | `tests/integration/test_identity_drift.py` | new Telegram handle → fuzzy proposal → owner confirms → re-binding |
| FR-RES-005 | covered by FR-ID-004 test | conversation merge correctness |

### 3.6 Cross-cutting NFRs

| NFR | Test file | Method |
|---|---|---|
| NFR-001 | `tests/perf/test_distillation_latency.py` | p95 < 1.5s on representative chat-turn workload |
| NFR-002 | `tests/perf/test_resolve_user_perf.py` | 10k-member org seed; p95 < 200ms |
| NFR-003 | `tests/integration/test_index_lag.py` | event emitted → query reflects ≤5s p95 |
| NFR-004 | `tests/integration/test_cross_tenant_privacy.py` | dedicated leak-attempt suite — every cross-tenant query path probed |
| NFR-005 | `tests/integration/test_no_delete_probes.py` | runs on every deploy + nightly schedule |
| NFR-006 | `tests/integration/test_purge_heartbeat.py` | absent heartbeat triggers alert |
| NFR-007 | `tests/integration/test_audit_immutability.py` | audit entries cannot be updated/deleted |
| NFR-008 | `tests/integration/test_principal_logging.py` | every DB call's structured log contains `principal_name` |
| NFR-009 | `tests/integration/test_policy_fail_mode.py` | per-policy fail mode applied on load failure |

---

## 4. API contract shapes (new endpoints)

Sketches for new endpoints — full OpenAPI generated by FastAPI from Pydantic models, but next session needs the shape.

### 4.1 Identity

```
POST /app/v1/identity/resolve_user
  Request: { query: str, hints?: object }
  Response: { candidates: ResolutionCandidate[] }

POST /app/v1/identity/merge_resource
  Request: { keep_id: str, merge_id: str, canonical_name?: str }
  Response: { tombstone_id: str, edges_redirected: int }

POST /app/v1/identity/register_alias
  Request: { node_id: str, alias: str }
  Response: { alias_count: int }
```

### 4.2 Conversations

```
GET /app/v1/conversations
  Query: ?counterparty_id=...&channel=...&thread_id=...&since=...
  Response: { entries: ConversationEntry[] }

GET /app/v1/conversations/index
  Response: { counterparties: { id, last_activity_at, channels[] }[] }
```

### 4.3 AgentChannelIdentity

```
GET /app/v1/admin/agent-channels?user_id=...
POST /app/v1/admin/agent-channels
  Request: { user_id, agent_id, channel, account_id, display_name, credentials_ref }
PATCH /app/v1/admin/agent-channels/{id}
DELETE /app/v1/admin/agent-channels/{id}     # ARCHIVE not delete (admin_principal)
```

### 4.4 Lifecycle (admin)

```
POST /app/v1/admin/lifecycle/cancel-purge       Request: { user_id }
POST /app/v1/admin/lifecycle/confirm-purge      Request: { user_id }
POST /app/v1/admin/lifecycle/right-to-erasure   Request: { user_id, justification, reauth_token }
POST /app/v1/admin/lifecycle/legal-hold/{node_id}    Request: { reason }
DELETE /app/v1/admin/lifecycle/legal-hold/{node_id}
POST /app/v1/admin/cross-tenant/rebuild         Request: { org_id? }
```

### 4.5 Auth (modified)

```
POST /app/v1/auth/login
  → Response 200: { token, ... }
  → Response 423 Locked (NEW): { purge_after, purge_initiated_at }   # pending purge
```

### 4.6 Policies

```
GET /app/v1/agents/{agent_id}/policies/{policy_name}
  Response: { frontmatter: object, body: str, version: int }

PUT /app/v1/agents/{agent_id}/policies/{policy_name}
  Request: { frontmatter: object, body: str, expected_version?: int }
  Response: { version: int }
```

### 4.7 Triggers (admin)

```
GET /app/v1/admin/triggers
POST /app/v1/admin/triggers/follow_up/configure
  Request: { user_id, default_follow_up_days?, interrupt_threshold_overrides?: ... }
```

### 4.8 External assignments

```
GET /app/v1/external-assignments
  Query: ?state=...&deadline_before=...&workspace_id=...
  Response: { assignments: ExternalAssignmentSummary[] }

GET /app/v1/external-assignments/{task_id}
  Response: { summary: ExternalTaskSummary }
```

---

## 5. Risk register

FRs flagged for elevated implementation risk; deserve extra design review and test coverage.

| FR | Risk | Mitigation |
|---|---|---|
| FR-DEL-002 | Postgres triggers are notoriously fragile across migrations and version upgrades. Trigger logic can be bypassed if a future PR uses superuser context inadvertently. | Trigger function tested under integration with `agent_principal` + `admin_principal` + `migration_principal`. Anti-delete CI probe covers UPDATE-of-lifecycle path explicitly. Code review checklist line item. |
| FR-AL-001 | Mandatory ACL at repo layer is invasive — every existing repo call site must be updated. Risk of incomplete migration silently bypassing ACL. | Refactor `repository.py` so `caller_context` is a required positional arg of every public method (compile-time enforcement via Pydantic / function signatures). Grep audit + AST audit in CI. |
| FR-XT-003 | Cross-tenant ACL bug = privacy incident. Zero-tolerance NFR-004. | Dedicated `tests/integration/test_cross_tenant_privacy.py` leak-attempt suite. Probes every query path with adversarial caller contexts. |
| FR-DEL-005 | Purge worker is destructive by design. Bug → data loss. | Three guards inside the txn (re-check `purge_after`, `legal_hold`, `purge_cancelled_at`). Idempotency via advisory lock. Staging dress-rehearsal before each production cutover. |
| FR-ID-004 | Merge is a many-step operation with cross-substrate effects (graph, MinIO conversations, Redis cache, active sessions). Partial failure leaves inconsistency. | Implement as saga with compensating actions. Each step idempotent. Test scenarios for each partial-failure point. |
| FR-IN-001 | Sender classification mistakes route a counterparty's message into owner's chat (or vice versa) — privacy breach + UX confusion. | Conservative defaults: ambiguous classification → `unknown_party`. Comprehensive routing-matrix test suite. |
| FR-RES-002 | Cross-channel reply lineage via content fingerprint is heuristic. False positives could mis-link a reply. | Surface low-confidence matches as "is this a reply to TSK-X?" prompt rather than auto-linking. |

---

## 6. Consolidated Open Questions log

Resolved (decided in design pass):

| # | Question | Decision | Reference |
|---|---|---|---|
| OQ-1 | Task ownership transfer on user deletion | Archive-tombstone with No-Delete principle | requirements §7, arch/19 |
| OQ-2 | Outbound channel-stickiness window | Configurable; default 48h | FR-GRAPH-005 |
| OQ-3 | Policy load failure mode | Per-policy `fail_mode: closed \| degraded` | FR-POL-001 |

To decide before / during the relevant wave:

| # | Question | Suggested wave | Default if not decided |
|---|---|---|---|
| OQ-4 | Backfill source for `UserNode.identities` for existing users | Wave 1 | Email from auth-time only; user adds others via Settings/onboarding refresh |
| OQ-5 | `OrgDirectoryVisibility` default for new orgs | Wave 8 | `OPEN` (current spec) — but reconsider for SaaS launch where privacy-conservative `consent-required` may be safer |
| OQ-6 | When User-1 (ORG-A) resolves Bob (ORG-A and ORG-B), do candidates show "also in ORG-B"? | Wave 8 | Show ORG-A only — the cross-org context isn't User-1's to know |
| OQ-7 | Multi-agent per user (Angela + Felicia) — shared or separate working memory? | Wave 9 | **Separate** per-agent memory under `{user_id}/agents/{agent_id}/memory/...` (current path scheme already supports this) |
| OQ-8 | Skill execution context — whose substrate does it write to? | Wave 4 | Owner's substrate; skill runs as agent_principal (FR-AP) |
| OQ-9 | Conversation `.jsonl` file growth bound | Wave 10 | Cap at last N (e.g., 500) entries with periodic compaction-to-summary archive into `conversations/{counterparty}/.archive/{ts}.jsonl` |
| OQ-10 | In-flight CheckinNodes pointing to a `merge_id` after merge | Wave 7 | `merge_resource` re-targets active checkins to `keep_id`; archived checkins keep their original target with redirect |
| OQ-11 | Org-creation flow in SaaS — invitation vs domain-claim vs link-share | Wave 8 / SaaS launch | Invitation-only initially; domain-claim added once SSO config UI exists |
| OQ-12 | Cross-channel reply via content fingerprint — auto-link threshold | Wave 10 | Auto-link only at high-confidence (≥0.85); below → user-confirm prompt |
| OQ-13 | `purge_after` default duration | Wave 0.5 | 24h (current spec) — confirm against legal/GDPR review |
| OQ-14 | Per-policy `fail_mode` defaults — review at Wave 4 | Wave 4 | delegation/escalation = `closed`; etiquette/tone = `degraded` (current spec) |

---

## 7. Cockpit-side implementation conventions (READ FIRST)

Cockpit uses specific patterns documented in [graphclaw-cockpit/CLAUDE.md](../../../graphclaw-cockpit/CLAUDE.md). When implementing any cockpit-side FR (UI panels, settings, admin views), the next session **must**:

1. **Read `cockpit/CLAUDE.md` first** — feature-based structure under `src/features/{name}/`, openapi-fetch + TanStack Query v5 for server state, Zustand v5 for client state, shadcn/ui (Radix + Tailwind v4) for components.
2. **Use the cockpit skills** that exist for these patterns:
   - `cockpit-api-integration` — openapi-fetch + TanStack Query patterns
   - `cockpit-react-patterns` — component structure, hooks, stores, shadcn/ui
   - `cockpit-playwright-e2e` — E2E test conventions
   - `cockpit-docker-deploy` — Docker patterns
3. **Follow naming**:
   - Components: `cockpit/src/features/{feature}/{Component}.tsx`
   - Tests co-located: `Component.test.tsx`
   - E2E: `cockpit/e2e/{feature}/*.spec.ts`
4. **Type generation**: openapi-fetch consumes the FastAPI-generated `openapi.json`. After backend changes, regenerate cockpit types.
5. **Commit format**: `feat(wave-N): description`.
6. **Pre-commit gate**: `npm run typecheck && npm run lint && npm run test` (cockpit) + `ruff check --fix && ruff format` (graphclaw — see `graphclaw-ruff-conventions` skill).

Cockpit FRs in the requirements doc that need this lens:
- FR-DEL-004 (PendingPurgeGate)
- FR-DEL-006 (RightToErasureFlow)
- FR-DEL-007 (LegalHoldPanel)
- FR-DEL-009 (OrgArchiveFlow)
- FR-IN-003 (AgentChannelsPanel)
- FR-POL-002 (PoliciesPanel + PolicyEditor)
- FR-SCHED-002 (PendingDecisionsBanner)
- FR-AM-001 (MultiAgentPanel)
- FR-UI-001 (CounterpartyConversations + ChannelTaggedHistory)
- FR-UI-002 (OrgSwitcher)
- FR-XT-004 (BriefingView extension)

---

## 8. Status tracking conventions

As waves are implemented, the requirements doc remains the source of truth. Convention:

- Each FR section gets a `**Status:**` line: `Pending` → `In Progress` → `Merged` → `Verified`.
- PRs reference FR-IDs in commit messages: `feat(wave-0): FR-DEL-002 lifecycle field triggers`.
- Wave completion: when all P0 FRs in a wave are `Verified` and acceptance criteria green, mark the wave heading `**Status: Complete**`.
- Open Questions log moves answered items from "To decide" to "Resolved" with the deciding wave + PR number.

---

## 9. Done — confirm before starting Wave 0

- [ ] Backups taken
- [ ] Feature flag default `false` set in env templates
- [ ] Anti-delete probe table migration drafted
- [ ] Risk register reviewed; high-risk FRs assigned an extra reviewer
- [ ] Open Questions log scanned for any that block Wave 0 (none today — OQ-1 is resolved)
- [ ] Read order confirmed: requirements doc → relevant arch doc (13–19) → plan file for rationale
- [ ] Cockpit conventions reviewed for any Wave 0 cockpit-side work (none — Wave 0 is backend-only)

When all checked: open W0-PR1 and proceed.
