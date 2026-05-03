# 15 — User Identity, Onboarding, Resolution

**Status:** Draft v1.0 | **Date:** 2026-05-02

This document specifies:
- Identity model: `UserNode` / `ResourceNode` with `identities`, `aliases`, `linked_user_id`
- Agent-led onboarding FSM (first-run experience)
- Name-resolution algorithm with org-directory lookup, disambiguation, alias-drift, merge
- Privacy: directory visibility, discoverability, consent

Companion docs: [13-tenancy-model.md](13-tenancy-model.md), [14-agent-triad.md](14-agent-triad.md), [16-cross-user-conversations.md](16-cross-user-conversations.md). Source plan §9.8.

---

## 1. Identity model

### 1.1 UserNode (the agent owner)
```python
class UserNode(BaseNode):
    name: str
    email: str
    role: str | None
    timezone: str
    working_hours: WorkingHours
    preferences: UserPreferences        # extended — see §1.4
    scoring_weights: ScoringWeights
    behavioral_model: BehavioralModel
    # NEW (FR-GRAPH-001):
    identities: ChannelIdentities = ChannelIdentities()
    # NEW (FR-GRAPH-002):
    aliases: list[AliasEntry] = []
```

### 1.2 ResourceNode (an entity that can own/work on tasks; may shadow a UserNode)
```python
class ResourceNode(BaseNode):
    resource_type: ResourceType
    name: str
    contact: str | None
    timezone: str | None
    capacity: CapacityModel
    reliability: ReliabilityModel
    current_risk: CurrentRisk
    communication_preferences: CommunicationPreferences
    # NEW (FR-GRAPH-001):
    identities: ChannelIdentities = ChannelIdentities()
    # NEW (FR-GRAPH-002):
    aliases: list[AliasEntry] = []
    # NEW (FR-GRAPH-003):
    linked_user_id: str | None = None      # cross-tenant shadow target
    link_status: LinkStatus = LinkStatus.ACTIVE
```

### 1.3 Supporting types
```python
class ChannelIdentities(BaseModel):
    emails: list[str] = []
    phones: list[str] = []
    telegram_id: str | None = None
    telegram_username: str | None = None
    whatsapp_id: str | None = None
    slack_user_id: str | None = None

class AliasEntry(BaseModel):
    value: str
    added_at: datetime
    added_by: str          # USER-{uuid} or "auto-fuzzy" for drift
    source: Literal["manual", "auto-fuzzy", "merge", "onboarding"]

class LinkStatus(str, Enum):
    ACTIVE = "active"
    DETACHED_USER_ARCHIVED = "detached_user_archived"
    DETACHED_USER_PURGED = "detached_user_purged"
    DETACHED_ORG_LEFT = "detached_org_left"
```

### 1.4 UserPreferences extensions (FR-GRAPH-005)
```python
discoverability: DiscoverabilityLevel = DiscoverabilityLevel.ORG_DEFAULT
channel_stickiness_hours: int = 48
channel_stickiness_overrides: dict[str, int] = {}    # per channel
```

`delegation_policy` is **NOT** here — it's a MinIO `.md` file (FR-POL-001) for editability via Intelligence Hub.

### 1.5 ResourceNode shadow semantics
When `linked_user_id` is set:
- **Read-through**: `preferences`, `identities`, `working_hours` are read from the linked UserNode (canonical).
- **Owner-specific**: `aliases`, owner-specific notes/intelligence stay on the shadow.
- **Detachment** (FR-AD-001): when linked UserNode is archived/purged, `link_status` flips and last-known canonical fields are frozen onto the shadow.

---

## 2. Onboarding FSM (FR-ID-001)

Triggered when a user signs in and `profile.md` is missing or has frontmatter `onboarding_complete: false`.

### 2.1 States
```
WELCOME → PERSONA → CHANNELS → WORKING_HOURS → PREFERENCES → POLICIES → DONE
```

### 2.2 Per-state contract
| State | Prompt file | Tool allow-list | Output written |
|---|---|---|---|
| WELCOME | `prompts/onboarding/welcome.md` | `set_user_name` | name confirmed |
| PERSONA | `prompts/onboarding/persona.md` | `set_user_persona` | profile.md body |
| CHANNELS | `prompts/onboarding/channels.md` | `add_user_identity` | `UserNode.identities` populated; `AliasResolver.register` for inbound |
| WORKING_HOURS | `prompts/onboarding/working_hours.md` | `set_working_hours` | `UserNode.working_hours` |
| PREFERENCES | `prompts/onboarding/preferences.md` | `set_preferences` | `UserNode.preferences` (briefing time, follow-up cadence, interrupt threshold) |
| POLICIES | `prompts/onboarding/policies.md` | `seed_policy_from_template` | `policies/*.md` seeded from defaults; user can edit later |
| DONE | n/a | `complete_onboarding` | profile.md frontmatter `onboarding_complete: true`; hand control to normal loop |

### 2.3 Resumability
State persisted in profile.md frontmatter:
```yaml
---
onboarding_complete: false
onboarding_state: PERSONA
onboarding_started_at: 2026-05-02T10:00:00Z
---
```
Quitting and returning resumes from `onboarding_state`.

### 2.4 Migration for existing users
Existing users with no `onboarding_complete` frontmatter → default to `true` on next load (no re-onboarding). One-shot script writes the frontmatter.

---

## 3. Resolution algorithm (FR-ID-002)

### 3.1 Steps
```
resolve_user(query, hints?, caller_user_id, caller_org_ids)
  → ResolutionCandidate[]

1. Local exact-alias hit
   Search caller's ResourceNode/UserNode aliases for exact value match.
   → If 1 match → return [confidence=1.0, source=local].

2. Local fuzzy match
   Trigram similarity over local nodes' name + aliases.
   → If top candidate ≥ HIGH_CONFIDENCE_THRESHOLD (0.85) and gap to #2
     is ≥ GAP_THRESHOLD (0.15) → return [confidence=top_score, source=local].
   → If multiple plausible → return top N for user disambiguation.

3. Org directory lookup
   Postgres trigram + embedding search over user_directory rows where
   org_id IN caller_org_ids (FR-DIR-002).
   Filter by visibility policy.
   → Return ranked candidates with source=org_directory.

4. New external person
   No match anywhere → enter create_person_via_dialog FSM (§4).

5. Alias-drift autoload (FR-ID-005)
   If resolution succeeded but the alias used wasn't on the matched node,
   append AliasEntry with provenance "auto-fuzzy".
```

### 3.2 ResolutionCandidate
```python
class ResolutionCandidate(BaseModel):
    node_id: str
    source: Literal["local", "org_directory"]
    confidence: float
    reason: str                # human-readable: "trigram match on name"
    display_name: str
    discriminators: dict       # role, workspace, email_domain, last_interaction
```

### 3.3 Cross-org scoping rule
`caller_org_ids` is computed from `OrganizationNode.members` — the caller's actual memberships. Org-directory query MUST intersect candidate's `org_id` with this list. A candidate not in any of caller's orgs is invisible.

---

## 4. create_person_via_dialog FSM (FR-ID-003)

Entered when resolution returns no high-confidence match.

### 4.1 States
```
DISAMBIGUATE → NAME → ROLE → CHANNEL → CONTACT → ALIASES → DONE
```

### 4.2 DISAMBIGUATE state — closes the Mr. Smith / Bob duplicate gap
**FIRST** prompt offers top-N existing local candidates with discriminators:
> "I don't know Bob. Is this someone new, or one of these?
>  • Mr. Smith (role: engineer, workspace: Engineering)
>  • Anita Cohen (role: PM, workspace: Project-X)
>  • Carlos Reyes (role: designer, workspace: Marketing)
>  Or **new person**."

User picks existing → alias-drift autoload (FR-ID-005) writes "Bob" onto the chosen node. Skip to DONE.
User picks new → continue NAME → ROLE → CHANNEL → CONTACT → ALIASES → DONE.

### 4.3 Outputs
- New ResourceNode (no `linked_user_id`) — pure external person, OR
- ResourceNode shadow with `linked_user_id` if matched in org directory and user confirmed link, OR
- Alias appended to existing node (DISAMBIGUATE shortcut).

---

## 5. merge_resource tool (FR-ID-004)

Post-hoc deduplication.

```python
merge_resource(keep_id, merge_id, canonical_name=None) -> MergeResult
```

### 5.1 Behavior
- All edges from `merge_id` redirected to `keep_id` (admin-side helper).
- `keep_id.aliases` gets `merge_id.aliases` deduplicated.
- `keep_id.intelligence` gets `merge_id.intelligence` chronologically merged.
- Counterparty conversations under `conversations/{user_id}/{merge_id}/...` append-merge-sorted into `conversations/{user_id}/{keep_id}/...` per channel (FR-RES-005).
- `merge_id` archived with `TombstoneNode { archived_id=merge_id, redirect_to=keep_id }`.
- Active comms-agent sessions on either node get cache-invalidation event so they reload context.
- If `canonical_name` provided, `keep_id.name` is updated; the previous name is preserved as an alias.

### 5.2 No-Delete compliance
The merge_id node is **archived**, never deleted. `resolve_canonical(merge_id)` returns `keep_id` thereafter. Per [arch/19](19-data-lifecycle-and-deletion-policy.md), the underlying record persists until purge worker acts (24h+).

---

## 6. Privacy & visibility

### 6.1 Org-level
`OrganizationNode.settings.directory_visibility` (FR-GRAPH-006):
- `open` (default), `name-only`, `consent-required`, `invitation-only`.

### 6.2 Per-user override
`UserNode.preferences.discoverability` can downgrade (never upgrade) the org default:
- `ORG_DEFAULT` (use org's setting)
- `NAME_ONLY` (force name-only regardless of org)
- `INVISIBLE` (excluded from directory entirely)

### 6.3 Notification on link
When User-1 first links Bob (creates a ResourceNode shadow with `linked_user_id`), Bob is notified per his policy (default: yes, via his preferred channel) — "USER-1 has added you as a contact in <org name>". Bob may dismiss or revoke (revoke flips link to invisible to User-1; pre-existing tasks remain).

---

## 7. Files

### Existing
| Concern | File |
|---|---|
| UserNode/ResourceNode models | [src/graphclaw/models/nodes.py](../../src/graphclaw/models/nodes.py) |
| AliasResolver (channel-identity) | [src/graphclaw/gateway/alias_resolver.py](../../src/graphclaw/gateway/alias_resolver.py) |
| Provisioning (atomic shell creation) | [src/graphclaw/auth/provisioning.py](../../src/graphclaw/auth/provisioning.py) |
| Profile.md path | [src/graphclaw/infra/storage.py](../../src/graphclaw/infra/storage.py) — `StoragePaths.agent_profile` |

### To create / modify
| FR | File | Action |
|---|---|---|
| FR-GRAPH-001 | [models/nodes.py](../../src/graphclaw/models/nodes.py) | Add `identities: ChannelIdentities` |
| FR-GRAPH-002 | [models/nodes.py](../../src/graphclaw/models/nodes.py) | Add `aliases: list[AliasEntry]` |
| FR-GRAPH-002 | new `src/graphclaw/models/types.py` | `AliasEntry` |
| FR-GRAPH-003 | [models/nodes.py](../../src/graphclaw/models/nodes.py) | `ResourceNode.linked_user_id`, `link_status` |
| FR-GRAPH-005 | [models/nodes.py](../../src/graphclaw/models/nodes.py) | `UserPreferences.discoverability`, `channel_stickiness_*` |
| FR-ID-001 | new `src/graphclaw/agent/onboarding.py` | FSM impl |
| FR-ID-001 | new `src/graphclaw/gateway/prompts/onboarding/{*.md}` | Per-state prompts |
| FR-ID-001 | new `src/graphclaw/agent/tools/onboarding_tools.py` | Tool impls |
| FR-ID-001 | [main_orchestrator.py](../../src/graphclaw/agent/main_orchestrator.py) | Detect first-run + route to FSM |
| FR-ID-002 | new `src/graphclaw/agent/tools/identity_tools.py` | `resolve_user`, `register_alias`, `merge_resource` tools |
| FR-ID-002 | new `src/graphclaw/identity/resolver.py` | Algorithm impl |
| FR-ID-003 | new `src/graphclaw/agent/identity/create_person.py` | FSM impl |
| FR-ID-004 | new `src/graphclaw/identity/merger.py` | Merge logic |
| FR-ID-004 | [db/age/repository.py](../../src/graphclaw/db/age/repository.py) | `redirect_edges` admin helper |
| FR-ID-005 | new `src/graphclaw/identity/resolver.py` | Post-resolution alias-drift hook |
| FR-DIR-001 | new `src/graphclaw/identity/directory.py` | Read API |
| FR-DIR-001 | new `src/graphclaw/identity/directory_indexer.py` | Event consumer |
| FR-DIR-002 | new `src/graphclaw/identity/resolver.py` | Cross-org scoping |
| FR-AD-001 | new `src/graphclaw/cascade/detachment.py` | Detach on archive |
