# 14 — Agent Triad: Comms / Inbound / Outbound

**Status:** Draft v1.0 | **Date:** 2026-05-02

GraphClaw's user-facing intelligence is delivered by three peer agents that share the same memory + graph substrate:

- **Comms agent** — the main orchestrator (per-user, `agent_id == user_id`). Plans, reasons, delegates, talks to the owner.
- **Inbound agent** — system-level. Classifies and distills inbound messages from any channel.
- **Outbound agent** — system-level. Handles all outbound dispatch with channel resolution, drafting, batching, and post-send writes.

All three write to the same `node.intelligence`, `working/context.md`, `conversations/...`, `CheckinNode` substrate, so context is automatically continuous regardless of which agent acted last.

Companion docs: [10-agent-loop-orchestration.md](10-agent-loop-orchestration.md), [11-sub-agent-orchestration.md](11-sub-agent-orchestration.md), [intelligence-layer.md](intelligence-layer.md), [16-cross-user-conversations.md](16-cross-user-conversations.md), [18-follow-up-cadence.md](18-follow-up-cadence.md), [20-agent-activity-logging.md](20-agent-activity-logging.md). Source plan §8.

> **2026-05-03 Agent Monitor v2 update.** All three triad agents now emit `agent.tool_call` structured logs at every TOOL_COMPLETED site (per [`docs/requirements/agent-monitor-v2-backend.md`](../requirements/agent-monitor-v2-backend.md) FR-6). Wiring sites: `comms_agent.py`, `inbound_agent.py`, `outbound_agent.py` (plus `main_orchestrator.py` and `sub_agent_runner.py`). The cockpit's Agent Monitor reads these from MinIO via `GET /app/v1/agent/activity` and displays them in the Activity panel. See [20-agent-activity-logging.md](20-agent-activity-logging.md) for the pipeline.

---

## 1. Architecture diagram

```
                 ┌──────────────────────────────────────────────┐
                 │            Memory + Graph substrate          │
                 │   working/context.md │ node.intelligence │   │
                 │   conversations/…    │ CheckinNode       │   │
                 └──────────────────────────────────────────────┘
                       ▲           ▲           ▲
                       │ reads     │ reads     │ reads
                       │ writes    │ writes    │ writes
        ┌──────────────┴───┐  ┌────┴───────┐  ┌┴────────────────┐
        │  Inbound Agent   │  │ Comms Agent│  │ Outbound Agent  │
        │  (1 system-wide) │─▶│ (per user) │─▶│ (1 system-wide) │
        └──────────────────┘  └────┬───────┘  └────────┬────────┘
                ▲                  │                   │
                │            invokes│             dispatches
                │                   ▼                   ▼
        channel adapters    sub-agents / skills /  channel adapters
        (email/wa/tg/web)        MCP / A2A         (email/wa/tg/web)
                                   ▲
                                   │ trigger payload
                          ┌────────┴────────┐
                          │  Scheduler /    │
                          │  Trigger Engine │
                          └─────────────────┘
```

---

## 2. Responsibilities

### 2.1 Comms agent (main orchestrator)
- One per user. `agent_id == user_id`. System header + per-user `profile.md`.
- Modes:
  - **owner_chat** — talking with the owner via cockpit, Telegram, WhatsApp, email
  - **counterparty_conversation** — talking with a counterparty on the owner's behalf (FR-CA-003)
  - **trigger** — invoked by scheduler (`trigger=follow_up_review`, `trigger=daily_briefing`)
  - **onboarding** — first-run experience (FSM, [arch/15](15-user-identity-and-onboarding.md))
- Tools: graph reads/writes (state-machine-gated), `delegate_to_agent`, `create_agent`, `invoke_skill`, `call_mcp_tool`, `send_message` (handoff to outbound), `archive_*` (no `delete_*` per [arch/19](19-data-lifecycle-and-deletion-policy.md))
- Post-turn distillation (FR-CA-002): writes `task_entry` and `memory_note` via shared distillation helper

### 2.2 Inbound agent
- One system-wide instance.
- Source: [src/graphclaw/inbound/intelligence_agent.py](../../src/graphclaw/inbound/intelligence_agent.py).
- Steps:
  1. **Classification (FR-IN-001)** — sender × reply-key × receiving-account → `RouteDecision`
  2. **Counterparty resolution (FR-IN-002)** — `(channel, sender_id) → ResourceNode | UserNode`
  3. **Task resolution** — Tier-1 reply-key → Tier-2 TaskID regex → Tier-3 vector search
  4. **Distillation** — `task_entry` to node intelligence, `memory_note` to working memory
  5. **Routing** — `user_chat` → wakes comms agent; `counterparty_reply` → optional comms wake; `counterparty_proactive` → wake comms in counterparty mode; `unknown_party` → escalate to owner

### 2.3 Outbound agent
- One system-wide instance, behaves as a peer agent (not just a dispatcher) — FR-OUT-001.
- Source after promotion: `src/graphclaw/agent/outbound.py` (refactor of existing `OutboundDispatcher`).
- Loop:
  1. Receive `OutboundIntent { task_id, recipient_id, purpose, draft? }`
  2. Resolve recipient → channel (FR-OUT-002): preference + stickiness window
  3. Evaluate delegation policy (FR-OUT-003) — reject or escalate if hard limits violated
  4. LLM-draft if no draft given (uses `outbound_profile.md` + tone policy)
  5. Honor batching window (`UserNode.preferences.channel_stickiness_*`)
  6. Dispatch via channel adapter
  7. Post-send hook (FR-OUT-004): `CheckinNode` create, Redis reply-key + persistent reply-lineage write, `node.intelligence` append, optional `memory_note`

---

## 3. Routing decision matrix (inbound)

| Sender match | Reply-key match | Receiving account → owner | Route |
|---|---|---|---|
| Owner's own identity | n/a | yes | `user_chat` → comms agent (owner mode) |
| Known counterparty | yes | yes | `counterparty_reply` → intelligence + optional comms wake |
| Known counterparty | no | yes | `counterparty_proactive` → comms agent (counterparty mode) |
| Unknown sender | n/a | yes | `unknown_party` → escalate to owner |
| Any | n/a | **no** | `drop` / dead-letter |

Receiving-account → owner mapping uses `AgentChannelIdentity` registry (FR-IN-003).

---

## 4. Distillation contract (shared between inbound + comms)

After every turn or message, two writes:

1. **Task-level intelligence** → append timestamped line to `node.intelligence` for the resolved task.
2. **Memory note** → append JSON-line entry under `## Recent Context` in `{user_id}/agents/{agent_id}/memory/working/context.md`.

Both go through the **distillation outbox** (FR-RES-001) with idempotency key `(message_id, target)` so a partial failure does not produce duplicates on retry.

The distillation logic is shared (FR-CA-002): one helper module, called from both `InboundIntelligenceAgent.process` and `MainOrchestrator.process_chat_message`.

---

## 5. Files

### Existing
| Concern | File |
|---|---|
| Comms agent loop | [src/graphclaw/agent/main_orchestrator.py:616](../../src/graphclaw/agent/main_orchestrator.py#L616) |
| System header (today) | [src/graphclaw/gateway/prompts/system_header.md](../../src/graphclaw/gateway/prompts/system_header.md) |
| Profile loader (Redis cache) | [src/graphclaw/agent/main_orchestrator.py:1101](../../src/graphclaw/agent/main_orchestrator.py#L1101) |
| Inbound agent | [src/graphclaw/inbound/intelligence_agent.py](../../src/graphclaw/inbound/intelligence_agent.py) |
| Inbound processor | [src/graphclaw/inbound/processor.py](../../src/graphclaw/inbound/processor.py) |
| Outbound (today as dispatcher) | [src/graphclaw/agent/outbound.py](../../src/graphclaw/agent/outbound.py) |
| Channel adapters | [src/graphclaw/gateway/channels/](../../src/graphclaw/gateway/channels/) |
| Existing trigger scaffold | [src/graphclaw/triggers/briefing.py](../../src/graphclaw/triggers/briefing.py), [src/graphclaw/agent/briefing.py](../../src/graphclaw/agent/briefing.py) |
| AliasResolver (channel-identity) | [src/graphclaw/gateway/alias_resolver.py](../../src/graphclaw/gateway/alias_resolver.py) |

### To create / modify
| FR | File | Action |
|---|---|---|
| FR-CA-001 | [main_orchestrator.py:616](../../src/graphclaw/agent/main_orchestrator.py#L616) | Extend signature to `(user_id, text, channel, thread_id, session_id)` |
| FR-CA-002 | new `src/graphclaw/agent/distillation.py` | Shared helper, replaces in-line logic |
| FR-CA-003 | new `src/graphclaw/gateway/prompts/system_header_counterparty.md` | Counterparty-mode prompt variant |
| FR-CA-003 | [main_orchestrator.py:1101](../../src/graphclaw/agent/main_orchestrator.py#L1101) | Accept `mode` param; inject policy bodies |
| FR-CA-003 | [tool_registry.py](../../src/graphclaw/agent/tool_registry.py) | `get_active_tools(mode)` filters per mode |
| FR-IN-001 | new `src/graphclaw/inbound/router.py` | `RouteDecision`, classification |
| FR-IN-001 | [processor.py:91](../../src/graphclaw/inbound/processor.py#L91) | Insert classification step |
| FR-IN-002 | [alias_resolver.py](../../src/graphclaw/gateway/alias_resolver.py) | Extend with `resolve_to_node` |
| FR-IN-003 | new `src/graphclaw/gateway/agent_channel_identity.py` | Registry service |
| FR-IN-003 | new `src/graphclaw/api/admin/agent_channels.py` | Admin CRUD |
| FR-OUT-001 | [outbound.py](../../src/graphclaw/agent/outbound.py) | Promote to `OutboundCommunicationAgent` |
| FR-OUT-001 | new `src/graphclaw/gateway/prompts/outbound_header.md` | System header for outbound agent |
| FR-OUT-001 | new `src/graphclaw/agent/outbound_intent.py` | `OutboundIntent` model |
| FR-OUT-002 | [outbound.py](../../src/graphclaw/agent/outbound.py) | `_resolve_channel` with stickiness |
| FR-OUT-003 | [outbound.py](../../src/graphclaw/agent/outbound.py) | Policy enforcement at entry |
| FR-OUT-004 | new `src/graphclaw/inbound/reply_keys.py` | Dual write Redis + Postgres reply_lineage |

---

## 6. Invocation contracts (sequence)

### Owner chat from cockpit (today's path, plus distillation)
```
cockpit POST /chat → api/chat.py → MainOrchestrator.process_chat_message(
  user_id, text, channel="cockpit", thread_id=session_id, session_id)
  → LLM loop → tools → reply
  → distillation outbox post (task_entry + memory_note)
→ HTTP response to cockpit
```

### Owner chat from Telegram
```
Telegram webhook → channels/telegram/adapter.py → InboundProcessor.process
  → InboundRouter.classify → user_chat
  → MainOrchestrator.process_chat_message(
      user_id, text, channel="telegram", thread_id=tg_chat_id, session_id)
  → LLM loop → tools → reply text
  → OutboundCommunicationAgent.dispatch_reply(thread_context, text)
  → distillation outbox post
```

### Counterparty proactive (Bob → Angela on Telegram)
```
Telegram webhook → InboundProcessor.process
  → InboundRouter.classify → counterparty_proactive
  → persist conversations/{user_id}/{Bob_id}/telegram/{thread}.jsonl
  → MainOrchestrator.process_counterparty_turn(
      user_id, counterparty_id=Bob, text, channel, thread_id, session_id)
  → LLM in counterparty_conversation mode (policy-loaded prompt)
  → policy-gated tools → optional outbound reply
  → distillation outbox post
```

### Scheduled follow-up (no user chat)
```
cron tick → FollowUpTrigger.run
  → query candidates (per-user follow_up_days)
  → MainOrchestrator.process_trigger(
      user_id, trigger="follow_up_review", payload={candidate_task_ids})
  → LLM loop → outbound dispatches
  → distillation outbox post
```
