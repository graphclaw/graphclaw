# 16 — Cross-User (Counterparty) Conversations

**Status:** Draft v1.0 | **Date:** 2026-05-02

This document specifies:
- Counterparty-scoped storage layout (per-counterparty, per-channel, per-thread)
- Routing decision matrix (inbound)
- Reply-key linking (Redis fast path + persistent Postgres long-tail)
- Counterparty conversation persistence and integration with the comms agent

Companion docs: [14-agent-triad.md](14-agent-triad.md), [15-user-identity-and-onboarding.md](15-user-identity-and-onboarding.md), [intelligence-layer.md](intelligence-layer.md), [19-data-lifecycle-and-deletion-policy.md](19-data-lifecycle-and-deletion-policy.md). Source plan §9.

---

## 1. Storage layout (FR-STORE-001)

Replace today's flat `{user_id}/chat/history.json` with a counterparty-scoped layout. Owner-self conversations live under the same scheme, keyed by the owner's own user_id — no special-case path.

```
{user_id_1}/                                          # owner of the agent
  agents/{user_id_1}/
    profile.md
    memory/working/context.md
  conversations/
    index.json                                        # counterparty list + last activity
    {user_id_1}/                                      # owner-self conversations
      cockpit/{thread_id}.jsonl
      telegram/{thread_id}.jsonl
      email/{thread_id}.jsonl
    {counterparty_id_Bob}/                            # counterparty conversations
      telegram/{thread_id}.jsonl
      email/{thread_id}.jsonl
    {counterparty_id_Carol}/
      ...
```

### 1.1 JSONL entry schema
```json
{
  "message_id": "msg_abc123",
  "ts": "2026-05-02T14:30:00Z",
  "direction": "in",
  "channel": "telegram",
  "thread_id": "tg_chat_456",
  "sender_id": "USER-bob-002",
  "content": "Pushing TSK-X to Friday — OK?",
  "task_refs": ["TSK-X"],
  "checkin_id": "CHK-789"
}
```

### 1.2 index.json
Per-owner lookup of counterparty → activity:
```json
{
  "USER-bob-002": {
    "last_activity_at": "2026-05-02T14:30:00Z",
    "channels": {
      "telegram": { "last_thread_id": "tg_chat_456", "msg_count": 12 },
      "email": { "last_thread_id": "em_thread_998", "msg_count": 3 }
    }
  },
  "RES-mrsmith-001": { ... }
}
```

### 1.3 Migration of existing chat history
`scripts/migrate_chat_history.py`:
1. For each existing user, read `{user_id}/chat/history.json`.
2. Append into `{user_id}/conversations/{user_id}/cockpit/{legacy}.jsonl` with `channel="cockpit"` tag.
3. Build `index.json` entry.
4. **Archive** (NOT delete) the legacy file per [arch/19](19-data-lifecycle-and-deletion-policy.md).

---

## 2. Routing decision matrix (FR-IN-001)

| Sender match | Reply-key match | Receiving account → owner | Route |
|---|---|---|---|
| Owner's own identity | n/a | yes | `user_chat` → comms agent (owner mode) |
| Known counterparty | yes | yes | `counterparty_reply` → intelligence + optional comms wake |
| Known counterparty | no | yes | `counterparty_proactive` → comms agent (counterparty_conversation mode) |
| Unknown sender | n/a | yes | `unknown_party` → escalate to owner via cockpit + preferred channel |
| Any | n/a | **no** | `drop` / dead-letter |

**Receiving-account → owner** uses `AgentChannelIdentity` registry (FR-IN-003): which Telegram bot / email mailbox / WhatsApp number maps to which `(user_id, agent_id)`.

**Sender → owner-self vs counterparty** uses:
1. `AliasResolver.resolve(channel, sender_id)` (Redis fast path)
2. Then `UserNode(user_id).identities` lookup (owner check)
3. Then `ResourceNode.identities` + `UserNode.identities` scoped to owner's substrate (counterparty check)

---

## 3. Reply-key linking (FR-OUT-004 + FR-RES-002)

### 3.1 Dual write at outbound time
Every outbound dispatch writes:
- **Redis (fast path, 7d TTL)**: `checkin:{channel}:{thread_id}:{msg_id}` → `{user_id, task_id, counterparty_id, checkin_id}`
- **Postgres `reply_lineage` (long-tail, no TTL)**: `(channel, thread_id) → {user_id, task_id, counterparty_id, content_fingerprint}`

### 3.2 Reply resolution tiers (inbound)
1. **Tier 1 — Redis reply-key** (fastest; works ≤7d after outbound)
2. **Tier 2 — Postgres reply_lineage by `(channel, thread_id)`** (long-tail; works any age within retention)
3. **Tier 3 — TaskID regex** in subject/body
4. **Tier 4 — Vector search** restricted to counterparty's tasks
5. **Tier 5 — Content-fingerprint cross-channel match** (FR-RES-002): if Bob replies via Telegram to an email outbound, fingerprint similarity proposes the link with confidence; orchestrator confirms.

---

## 4. Counterparty conversation lifecycle

### 4.1 First contact (counterparty proactive — Scenario B)
1. Telegram webhook → InboundProcessor.
2. Receiving bot → AgentChannelIdentity → owning user_id.
3. Sender → resolved as ResourceNode in owner's substrate (or org directory).
4. Route = `counterparty_proactive`.
5. Conversation file created: `conversations/{user_id}/{counterparty_id}/telegram/{thread_id}.jsonl`.
6. Comms agent woken in `counterparty_conversation` mode.

### 4.2 Outbound-initiated (Scenario A)
1. Comms agent calls `outbound.send(OutboundIntent { task_id, recipient=Bob, purpose })`.
2. Outbound resolves channel + dispatches.
3. Conversation entry written; CheckinNode created; reply-key dual-written.
4. Bob replies → Tier-1 reply-key match → `counterparty_reply` route.

### 4.3 Mode transitions
The comms agent's `process_counterparty_turn` (FR-CA-003) loads:
- Counterparty profile from ResourceNode (read-through if `linked_user_id` set)
- Owner's policies (delegation/etiquette/tone/escalation) from MinIO
- Recent thread context from `conversations/{owner}/{counterparty}/{channel}/{thread}.jsonl` (last N entries)
- Cross-cutting working memory `working/context.md` (full)

Tool allow-list per FR-CA-003.

### 4.4 Merge implications (FR-RES-005)
When `merge_resource(keep_id=Mr.Smith, merge_id=Bob)` runs:
- `conversations/{user_id}/{Bob}/telegram/{thread}.jsonl` content is **append-merge-sorted by ts** into `conversations/{user_id}/{Mr.Smith}/telegram/{thread}.jsonl`.
- A `.tombstone` file written at `conversations/{user_id}/{Bob}/` redirecting to `Mr.Smith`.
- Original Bob `.jsonl` files **archived** (NOT deleted).

---

## 5. Cross-tenant counterparty handling (Scenario 2 from validation)

When `counterparty_id` is a ResourceNode with `linked_user_id = USER-bob-002`:
- Conversation persists under `{user_id_1}/conversations/{Bob_shadow_id}/...` (User-1's substrate).
- Bob's own substrate (`{user_id_bob}/...`) is **NOT** touched by this write.
- Bob's agent (Brian) sees TSK-X via `list_external_assignments_for_me()` (FR-XT-002) — task-level visibility, not conversation-level.
- Conversation-level visibility for Bob would require explicit cross-tenant share (out of scope for v1).

---

## 6. Files

### Existing
| Concern | File |
|---|---|
| Today's flat chat history | [src/graphclaw/api/chat.py](../../src/graphclaw/api/chat.py) |
| Inbound processor | [src/graphclaw/inbound/processor.py](../../src/graphclaw/inbound/processor.py) |
| Outbound dispatcher | [src/graphclaw/agent/outbound.py](../../src/graphclaw/agent/outbound.py) |
| AliasResolver | [src/graphclaw/gateway/alias_resolver.py](../../src/graphclaw/gateway/alias_resolver.py) |

### To create / modify
| FR | File | Action |
|---|---|---|
| FR-STORE-001 | [infra/storage.py](../../src/graphclaw/infra/storage.py) | New paths: `conversation_thread`, `conversation_index`, `conversation_counterparty_dir` |
| FR-STORE-001 | [api/chat.py](../../src/graphclaw/api/chat.py) | Persist channel-tagged into new layout |
| FR-STORE-001 | new `scripts/migrate_chat_history.py` | One-shot migration with archive of legacy |
| FR-IN-001 | new `src/graphclaw/inbound/router.py` | RouteDecision logic |
| FR-IN-001 | [inbound/processor.py:91](../../src/graphclaw/inbound/processor.py#L91) | Insert classification |
| FR-IN-002 | [gateway/alias_resolver.py](../../src/graphclaw/gateway/alias_resolver.py) | `resolve_to_node` |
| FR-IN-003 | new `src/graphclaw/gateway/agent_channel_identity.py` | Registry |
| FR-OUT-004 | new `src/graphclaw/inbound/reply_keys.py` | Dual write |
| FR-RES-002 | new migration `0XX_reply_lineage.py` | Postgres table |
| FR-RES-005 | new `src/graphclaw/identity/merger.py` | Conversation-merge logic |
| FR-UI-001 | `cockpit/src/features/tasks/CounterpartyConversations.tsx` | Cockpit view |
