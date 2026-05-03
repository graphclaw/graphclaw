# Review: Main Orchestrator Agent — Design Philosophy vs. Implementation

> **Role in project documentation.** This is the **design-conversation transcript** that produced the comms/inbound/outbound triad design, the No-Delete principle, and the related architecture extensions (arch docs 13–19). It captures gaps (A–AX), validation walkthroughs, stress tests, and design rationale that the actionable spec ([agent-triad-and-comms-substrate.md](agent-triad-and-comms-substrate.md)) distills into FR-IDs. Kept here permanently as the **why** companion to the requirements doc's **what** and the architecture docs' **how**.
>
> When refactoring documentation later: this file's content can be reorganized into design-decision records (ADRs) per concern, but should not be discarded — the validation walkthroughs and stress-test scenarios are difficult to reconstruct from the distilled docs alone.
>
> **Companions:**
> - [agent-triad-and-comms-substrate.md](agent-triad-and-comms-substrate.md) — tracked requirements with FR-IDs, files, acceptance, wave plan
> - [arch/13-tenancy-model.md](../architecture/13-tenancy-model.md) → [arch/19-data-lifecycle-and-deletion-policy.md](../architecture/19-data-lifecycle-and-deletion-policy.md) — design specifications

---

## Context

You asked for a review of the design philosophy of the **main orchestrator agent** in GraphClaw, comparing your stated mental model against (a) the architecture/PRD documents and (b) the actual implementation, with the goal of finding gaps and identifying which docs/implementation files need to be brought back in sync.

> **Foundational principle locked in (§9.8.16.5):** **No agent ever performs a hard delete.** Agents connect via a service principal that has NO delete grants at the database level (Postgres `REVOKE DELETE`, AGE label-level grants, MinIO `s3:DeleteObject` denied). All "removal" is archive + tombstone; user-initiated full purge is a 24h-delayed operation by a separate admin principal. This is GDPR-compliant, audit-friendly, and removes a class of catastrophic-action failure modes from any LLM-driven turn. **This principle propagates through every Gap and PR in this plan** — see Wave 0 in the requirements doc.

The single most important question was: **does the main orchestrator distill user↔agent chat into per-task node intelligence and into a timeline-based working memory?** Short answer: **the design says yes, but the implementation does this only for inbound channel messages (email/Slack/etc.) via a separate agent — the main orchestrator's own chat path does NOT distill into node intelligence or working-memory timeline.** That is the central gap.

---

## 1. Your mental model — confirm / refine

| Your statement | Verdict | Notes |
|---|---|---|
| Main orchestrator is a system-defined agent with a system header + per-user `{user-id}` profile | **Refined** | There is a system header ([system_header.md](../../../Projects/graphclaw/src/graphclaw/gateway/prompts/system_header.md)) AND a per-user profile, but the orchestrator is not a single global "system agent that takes a user-id parameter." Per [agent-subagent-design-requirements.md §2.3](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md), **each user has their own primary agent with `agent_id == user_id`** (e.g., `USER-dev-001`). The system header is the same for everyone; the profile at `{user_id}/agents/{user_id}/profile.md` is per-user. |
| Job: communicate with user, plan/reason, give updates, take inputs, confirm plans, delegate to sub-agents / tools / skills / A2A | **Confirmed** | Implemented in [main_orchestrator.py:616](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L616) (`process_chat_message`). Tool registry exposes graph tools, `delegate_to_agent`, `create_agent`, `invoke_skill`, `call_mcp_tool`, `send_email`, `post_slack_message`. |
| Inbound communication agent processes email/WhatsApp/Telegram, updates task graph + stores context in nodes | **Confirmed in design, partial in code** | [InboundIntelligenceAgent](../../../Projects/graphclaw/src/graphclaw/inbound/intelligence_agent.py) implements the resolver waterfall (reply chain → TaskID regex → vector search) and writes both `node.intelligence` and `working/context.md`. Email + Slack + Teams are wired; **WhatsApp + Telegram have adapter stubs but inbound polling is not wired into the gateway loop**. |
| **Main orchestrator stores task-level intelligence into nodes and distills chats into a timeline in working memory** | **❌ NOT IMPLEMENTED for chat path** | Design (`intelligence-layer.md` §2) calls for this dual-tier write on every message — including web chat. But [process_chat_message](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L616) only: builds system prompt → calls LLM → executes tools → returns reply. It never invokes `InboundIntelligenceAgent`, never appends to `node.intelligence`, never appends to `working/context.md`. Chat history goes to a flat `{user_id}/chat/history.json` (last 200 msgs) via the REST endpoint instead. |
| Outbound communication agent invoked by orchestrator, channel chosen from user-node preferences | **Partial** | [OutboundDispatcher](../../../Projects/graphclaw/src/graphclaw/agent/outbound.py) dispatches to email/Telegram/Slack and logs to `node.intelligence` + creates `CheckinNode`. **However, channel selection is hardcoded at the call-site by the LLM/tool args.** It does NOT consult `UserNode.preferences` or `ResourceNode.communication_preferences` to auto-route. |

---

## 2. Architecture & design — reference map

These are the authoritative documents (in the **graphclaw** repo, not cockpit):

- [docs/architecture/10-agent-loop-orchestration.md](../../../Projects/graphclaw/docs/architecture/10-agent-loop-orchestration.md) — orchestrator turn loop, system-prompt assembly
- [docs/architecture/intelligence-layer.md](../../../Projects/graphclaw/docs/architecture/intelligence-layer.md) — **the dual-tier (node intelligence + agent working memory) design**, resolver waterfall, distillation post-processing
- [docs/agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md) — primary-agent identity, profile/memory layout

---

## 3. Implementation map (current state)

| Concern | File | State |
|---|---|---|
| Orchestrator turn loop | [main_orchestrator.py:616](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L616) | ✅ |
| Profile loading | [main_orchestrator.py:1101](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L1101) | ✅ Redis-cached 15min |
| Inbound intelligence distillation | [inbound/intelligence_agent.py](../../../Projects/graphclaw/src/graphclaw/inbound/intelligence_agent.py) | ✅ |
| `node.intelligence` write | [db/age/repository.py:504](../../../Projects/graphclaw/src/graphclaw/db/age/repository.py#L504) | ✅ |
| `working/context.md` append | [inbound/intelligence_agent.py:514](../../../Projects/graphclaw/src/graphclaw/inbound/intelligence_agent.py#L514) | ✅ |
| Chat persistence | [api/chat.py](../../../Projects/graphclaw/src/graphclaw/api/chat.py) → `{user_id}/chat/history.json` | ⚠️ flat log, not distilled |
| Outbound dispatcher | [agent/outbound.py](../../../Projects/graphclaw/src/graphclaw/agent/outbound.py) | ⚠️ no preference-driven routing |
| Email channel | [gateway/channels/email/](../../../Projects/graphclaw/src/graphclaw/gateway/channels/email/) | ✅ |
| Slack/Teams channels | [gateway/channels/{slack,teams}/](../../../Projects/graphclaw/src/graphclaw/gateway/channels/) | ✅ |
| WhatsApp/Telegram channels | [gateway/channels/{whatsapp,telegram}/](../../../Projects/graphclaw/src/graphclaw/gateway/channels/) | ❌ stubs, inbound poller not wired |

---

## 4. Gaps to fix (ordered by impact)

### Gap A — Orchestrator chat path does not feed the intelligence layer (HIGHEST)
The orchestrator's `process_chat_message` should, on every turn, run the same distillation that `InboundIntelligenceAgent` runs for inbound channel messages:
- Extract a per-task `task_entry` (when a task can be resolved from the chat content) → prepend to `node.intelligence` via `graph_repo.update_node_intelligence`.
- Extract a `memory_note` (general behavioural / preference / cross-task observation) → append (under `_memory_lock`) to `{user_id}/agents/{user_id}/memory/working/context.md`.

Two viable shapes:
1. **Inline post-processing** in `process_chat_message` after the agentic loop returns its final reply.
2. **Reuse `InboundIntelligenceAgent`** by routing web-chat through `POST /inbound/messages` with `channel="web"` (PRD 13 already implies this — "Maps to existing `InboundMessage`"). This is the cleaner path.

Files to touch: [main_orchestrator.py:616](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L616), [api/chat.py](../../../Projects/graphclaw/src/graphclaw/api/chat.py), or [inbound/processor.py](../../../Projects/graphclaw/src/graphclaw/inbound/processor.py).

### Gap B — Outbound dispatcher ignores user/resource channel preferences
Add a resolver in [agent/outbound.py:180](../../../Projects/graphclaw/src/graphclaw/agent/outbound.py#L180): when `channel` is unspecified or `auto`, look up the recipient's `UserNode.preferences` or `ResourceNode.communication_preferences.preferred_channel` from the graph and route accordingly. Honour `batch_messages` / `batch_window_hours`.

### Gap C — WhatsApp / Telegram inbound polling not wired
Adapters exist; gateway does not start their pollers. Wire into the gateway lifespan startup alongside the email IMAP poller.

### Gap D — Chat history is duplicative with intelligence layer
`{user_id}/chat/history.json` (last 200) and the intelligence layer (working memory + node intelligence) coexist with no defined relationship. Pick one of:
- Keep `chat/history.json` as the **raw transcript** and treat the intelligence layer as the **distilled** representation, or
- Replace `chat/history.json` with reads from working memory + per-task `intelligence_log`.

### Gap E — Outbound also lacks `memory_note` writebacks for chat-driven sends
When the orchestrator sends an outbound message *as a result of a chat decision*, the design says both the `CheckinNode` link and a working-memory entry should be created. Currently only `node.intelligence` is appended.

---

## 5. Documentation that needs updating

| Doc | Update needed |
|---|---|
| [graphclaw/docs/architecture/10-agent-loop-orchestration.md](../../../Projects/graphclaw/docs/architecture/10-agent-loop-orchestration.md) | Add an explicit "post-turn distillation" step to the orchestrator loop, mirroring §4.4 of intelligence-layer.md. |
| [graphclaw/docs/architecture/intelligence-layer.md](../../../Projects/graphclaw/docs/architecture/intelligence-layer.md) | Clarify that distillation applies to **all message sources including web chat**, not only inbound channels. Add an "Outbound preference resolution" subsection. |
| [cockpit/docs/prd/13-chat-interface.md](docs/prd/13-chat-interface.md) | Already says web chat maps to `InboundMessage`; add a paragraph stating the chat is processed by `InboundIntelligenceAgent` with the same dual-tier write semantics, so the front-end correctly conveys what gets persisted where. |
| [cockpit/docs/prd/15-intelligence-hub.md](docs/prd/15-intelligence-hub.md) | Currently describes the Hub as read/edit access to memory & profile; add note that **chat-driven distillation is the primary write path** so users understand what populates Working/Episodic memory. |
| [cockpit/docs/prd/03-agent-monitor.md](docs/prd/03-agent-monitor.md) | Add observability surface for "distillation events" (task_entry written / memory_note written), not just tool calls. |
| [graphclaw/docs/agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md) | Reaffirm that the primary agent is responsible for distillation regardless of channel; add an outbound-preference contract with the User/Resource node schema. |
| New doc (or section) — **Outbound Communication Agent contract** | There is no single doc tying together OutboundDispatcher behaviour, user/resource preference resolution, batch windows, CheckinNode creation, and intelligence logging. Worth writing one. |

---

## 6. Verification — how to confirm the gaps after fix

- **Distillation in chat path**: send a chat message that references a known task ID → after reply returns, `GET /app/v1/graph/nodes/{task_id}` should show a new line in `intelligence`, and `GET /app/v1/intelligence/memory/working` should show a new JSON-line note. Add a unit test in `tests/agent/test_main_orchestrator.py` asserting both writes happen.
- **Outbound preference routing**: create a `UserNode` with `preferred_channel="telegram"`; have the orchestrator call `send_message` without specifying channel; assert it dispatches via `TelegramSender`.
- **WhatsApp/Telegram inbound polling**: with adapters configured, send a message into the configured WhatsApp/Telegram bot → confirm it lands in `INBOUND_MESSAGES` queue and produces a `node.intelligence` update.
- **Docs sync**: open each doc listed in §5, check that the described behaviour now matches code.

---

## 7. Critical files (reference)

- [src/graphclaw/agent/main_orchestrator.py](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py) — orchestrator entry points
- [src/graphclaw/inbound/intelligence_agent.py](../../../Projects/graphclaw/src/graphclaw/inbound/intelligence_agent.py) — distillation reference implementation to be reused
- [src/graphclaw/inbound/processor.py](../../../Projects/graphclaw/src/graphclaw/inbound/processor.py) — inbound pipeline that web chat should route through
- [src/graphclaw/api/chat.py](../../../Projects/graphclaw/src/graphclaw/api/chat.py) — current chat persistence to be unified
- [src/graphclaw/agent/outbound.py](../../../Projects/graphclaw/src/graphclaw/agent/outbound.py) — preference-aware routing
- [src/graphclaw/models/nodes.py](../../../Projects/graphclaw/src/graphclaw/models/nodes.py) — `UserNode.preferences`, `ResourceNode.communication_preferences`, `TaskNode.intelligence`
- [src/graphclaw/db/age/repository.py:504](../../../Projects/graphclaw/src/graphclaw/db/age/repository.py#L504) — `update_node_intelligence`
- [src/graphclaw/infra/storage.py](../../../Projects/graphclaw/src/graphclaw/infra/storage.py) — `StoragePaths` for memory/profile/archive
- [src/graphclaw/triggers/briefing.py](../../../Projects/graphclaw/src/graphclaw/triggers/briefing.py) — existing trigger/cadence scaffolding to extend for follow-ups
- [src/graphclaw/agent/briefing.py](../../../Projects/graphclaw/src/graphclaw/agent/briefing.py) — existing daily-briefing path; closest precedent for scheduled comms-agent invocation
- [src/graphclaw/agent/event_consumer.py](../../../Projects/graphclaw/src/graphclaw/agent/event_consumer.py) — already updates node intelligence on trigger events; reuse pattern

---

## 8. Triad pattern: Comms / Inbound / Outbound as peer system agents

Your follow-up reframes the architecture from "one orchestrator + helpers" to a **three-agent triad**, with the comms agent (main orchestrator) at the centre and inbound/outbound as peer system-level agents that share the same memory + graph substrate. This is the right shape; the gaps below describe what changes to land it.

### 8.1 Three system agents, one shared substrate

| Agent | Trigger | Inputs | Outputs | LLM-driven? |
|---|---|---|---|---|
| **Inbound** (`InboundIntelligenceAgent`) | Any inbound message on any channel | normalized `InboundMessage` | (a) resolved `task_id`, (b) `node.intelligence` line, (c) `working/context.md` note, (d) routing decision: *intelligence-only* vs. *deliver to comms agent* | yes (already) |
| **Comms** (main orchestrator) | (a) user message routed by inbound, (b) cockpit chat REST, (c) scheduler tick, (d) trigger/event | (text or trigger payload) + working memory + graph | reply text + tool calls (delegate, skill, MCP, **invoke outbound**) | yes (already) |
| **Outbound** (`OutboundCommunicationAgent` — promote from `OutboundDispatcher`) | comms agent tool call OR scheduler/trigger direct call | recipient (user/resource), task_id, intent ("follow_up", "decision_request", "notification"), optional draft | dispatched message + `CheckinNode` + `node.intelligence` line + (optional) `memory_note` | **yes — new**: pick channel from prefs, draft if no draft given, batch by window, set Redis `checkin:{msg_id}` for reply linking |

Key principle: **each of the three writes to the same memory + graph substrate** (working/context.md, node.intelligence, CheckinNode), so context is automatically continuous regardless of who acted last.

### 8.2 Outbound as a true peer agent (not just a dispatcher)

Today `OutboundDispatcher` is a thin function that picks an adapter and sends bytes. It should grow into a peer agent with the same shape as the inbound agent:

- **System prompt + profile**: `system/prompts/outbound_header.md` + per-user `{user_id}/agents/{user_id}/outbound_profile.md` (tone, signature, escalation rules).
- **Loop**: receive `OutboundIntent { task_id, recipient_id, purpose, draft? }` → resolve channel from `UserNode.preferences` / `ResourceNode.communication_preferences` (with override for explicit `channel`) → draft/refine via LLM if no draft → check batch window → dispatch via channel adapter → write `CheckinNode` + `node.intelligence` + Redis reply key → emit `agent.outbound_sent` log.
- **Tools** (small set): `get_node_details`, `get_user_preferences`, `get_pending_outbound_for_recipient` (for batching), `dispatch_via_channel`, `create_checkin_node`, `update_node_intelligence`.
- **Reuses** the same `StorageClient`, `GraphRepo`, channel adapters as today.

This makes the comms agent's `send_message` tool a thin handoff: it builds an `OutboundIntent` and enqueues it (sync or async via broker), exactly like `delegate_to_agent` works for sub-agents.

### 8.3 Scheduler / cadence-driven follow-ups

There is already a trigger scaffold in [triggers/briefing.py](../../../Projects/graphclaw/src/graphclaw/triggers/briefing.py) and [agent/briefing.py](../../../Projects/graphclaw/src/graphclaw/agent/briefing.py) — extend it rather than build a parallel scheduler. Add a `FollowUpTrigger`:

1. **Cron tick** (e.g., every 15 min, configurable per user via `UserNode.preferences.briefing_time` analogue).
2. **Candidate selection** (graph query): tasks where `state ∈ {WAITING, IN_PROGRESS}` AND `now - last_outbound_at >= follow_up_days` AND `state != BLOCKED_BY_USER`. Use existing `default_follow_up_days` and `interrupt_threshold` on `UserNode.preferences`.
3. **Hand to comms agent** with a synthetic system message:
   `trigger=follow_up_review, candidate_task_ids=[...]`
   The comms agent runs its normal loop — reasons about which to follow up on, drafts intent, calls `send_message` (→ outbound agent). Critically, **the same distillation post-step in §4 Gap A applies**, so the cadence run leaves the same memory/intelligence trail as a chat-driven run.
4. **Outbound agent** does the actual send + CheckinNode + intelligence write.
5. **Reply** comes back through inbound agent (Tier-1 Redis reply-chain match → links to the same task), updates `node.intelligence`, optionally re-wakes the comms agent if the reply is from the user themselves.

This means **the user does not need to be in the cockpit for the follow-up to happen** — the triad runs headlessly on the cron tick and only surfaces to the user via outbound channels (and a feed entry in the cockpit when they next open it).

### 8.4 Multi-channel chat WITH the comms agent (WhatsApp / Telegram / email)

The crucial distinction the inbound agent must make is **who the message is from**:

- **User-as-self** (the user pinging their own agent on WhatsApp/Telegram/email): this should behave exactly like cockpit chat. Route to the comms agent's loop, deliver the reply through the **same channel** via the outbound agent. Memory/context is shared because both sides write to the same `{user_id}/agents/{user_id}/...` substrate.
- **Third-party** (an external person responding to a checkin or proactively writing in): the inbound agent does intelligence-only writes (task_entry + memory_note). It does NOT wake the comms agent unless the message is high-confidence-relevant AND crosses `interrupt_threshold`, in which case it surfaces a notification card in the cockpit feed and (optionally) fires an outbound to the user via their preferred channel.

Concretely:

- Add a router step at the top of `InboundProcessor.process` that classifies sender via known identities on `UserNode` (user's own email, phone, telegram_id) → emits one of:
  - `RouteDecision.user_chat` → publish to `CHAT_MESSAGES` queue with `channel`/`thread_id`; the comms agent's chat-handler subscribes (just like the cockpit REST endpoint does today).
  - `RouteDecision.intelligence_only` → existing path.
  - `RouteDecision.escalate` → intelligence write + cockpit notification + optional outbound to user.
- The comms agent's `process_chat_message` becomes channel-agnostic: it accepts `(user_id, text, channel, thread_id, session_id)`. The reply is sent through the outbound agent on the originating `channel` + `thread_id` — not always to the cockpit.
- **Thread / session continuity**: store `thread_id` per channel on the chat history entry. Working memory remains a single per-user file, so context bridges channels naturally; chat history can either be one merged log tagged by channel, or one log per `(user_id, channel, thread_id)`. Recommended: **one merged log tagged by channel** so the comms agent always sees full cross-channel history when answering, while threads remain addressable for delivery.

### 8.5 Wiring summary (target state)

```
                    ┌──────────────────────────────────────────────┐
                    │            Memory + Graph substrate          │
                    │   working/context.md │ node.intelligence │   │
                    │   chat history       │ CheckinNode       │   │
                    └──────────────────────────────────────────────┘
                          ▲           ▲           ▲
                          │ reads     │ reads     │ reads
        writes ───────────┘ /writes   │ /writes   │ /writes
        ┌─────────────┐      ┌────────┴─────┐    ┌────────┴────────┐
        │  Inbound    │      │   Comms      │    │   Outbound      │
        │  Agent      │─────▶│   Agent      │───▶│   Agent         │
        │             │      │ (orchestr.)  │    │                 │
        └─────────────┘      └──────┬───────┘    └────────┬────────┘
            ▲                       │                     │
            │                       │ invokes             │ dispatches
            │                       ▼                     ▼
       channel adapters     sub-agents / skills /    channel adapters
       (email/wa/tg/web)         MCP / A2A          (email/wa/tg/web)
                                  ▲
                                  │ trigger payload
                          ┌───────┴────────┐
                          │  Scheduler /   │
                          │  Trigger Engine│  (briefings, follow-ups, deadlines)
                          └────────────────┘
```

### 8.6 Additional gaps this section introduces (append to §4)

- **Gap F — Outbound is not yet a peer agent.** Promote `OutboundDispatcher` → `OutboundCommunicationAgent` with system prompt, profile, LLM-driven drafting, batching policy, CheckinNode + intelligence + Redis reply-key writes.
- **Gap G — No inbound sender-classification router.** Add `RouteDecision { user_chat | intelligence_only | escalate }` step in `InboundProcessor` that uses `UserNode` known identities.
- **Gap H — Comms agent chat handler is web-only.** Generalize `process_chat_message(user_id, text, channel, thread_id, session_id)`; deliver reply through outbound agent on the same channel.
- **Gap I — No follow-up trigger.** Add `FollowUpTrigger` alongside `BriefingTrigger`; query candidates, invoke comms agent with `trigger=follow_up_review` payload.
- **Gap J — Chat history not channel-tagged.** Add `channel` + `thread_id` fields to chat history entries; keep one merged per-user log (so comms agent sees cross-channel context) addressable by `(channel, thread_id)` for delivery.
- **Gap K — User identity registry on `UserNode` is sparse.** Add `identities: { email[], phone[], telegram_id?, whatsapp_id?, slack_user_id? }` so the inbound router can classify confidently.

### 8.7 Additional docs to update (append to §5)

- **New doc** — `graphclaw/docs/architecture/agent-triad.md`: the inbound/comms/outbound triad, shared substrate, message-flow diagrams (incl. §8.5 diagram).
- **New doc** — `graphclaw/docs/architecture/follow-up-cadence.md`: scheduler design, candidate-selection query, interrupt-threshold semantics, integration with triggers.
- Update [agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md): treat outbound as a peer agent, not a tool.
- Update [intelligence-layer.md](../../../Projects/graphclaw/docs/architecture/intelligence-layer.md): add the sender-classification router and explicit "all three agents write to the same substrate" guarantee.
- Update [cockpit/docs/prd/13-chat-interface.md](docs/prd/13-chat-interface.md): web chat is one channel; document that the comms agent maintains unified context across cockpit, WhatsApp, Telegram, email, and that replies flow back on the originating channel.
- Update [cockpit/docs/prd/15-intelligence-hub.md](docs/prd/15-intelligence-hub.md): show that working memory accumulates from all three agents and all channels.

### 8.8 Verification additions (append to §6)

- **Triad smoke test**: send a WhatsApp message *as the user* → assert it lands in the comms agent loop, reply is dispatched via WhatsApp by the outbound agent, both inbound and outbound entries appear in `node.intelligence`, working memory has a new note.
- **Follow-up cadence test**: seed a task with `last_outbound_at` older than `follow_up_days`, advance the scheduler clock → assert outbound agent dispatches a follow-up and a `CheckinNode` is created.
- **Sender classification test**: send the same WhatsApp message from (a) the user's known phone and (b) an unknown phone → assert (a) routes to comms agent and (b) goes intelligence-only.

---

## 9. Cross-user (counterparty) conversations

The triad in §8 implicitly assumes the only chat partner is the agent's owner. Real flows involve **the agent talking to other people on the owner's behalf** — e.g., Angela (User-1's agent) following up Bob (User-2) on Telegram. Bob's reply must land in User-1's substrate but be **kept separate from User-1's own chats with Angela**, and proactive pings from Bob must also be handled. The current `{user_id}/chat/history.json` cannot represent this.

### 9.1 Worked scenarios

**Scenario A — outbound-initiated (Angela → Bob → Angela)**
1. User-1 asks Angela (in cockpit chat) to follow up Bob on `TSK-123`.
2. Comms agent calls outbound agent with `OutboundIntent { task_id=TSK-123, recipient_id=Bob, purpose=follow_up }`.
3. Outbound agent reads Bob's preferred channel from `ResourceNode(Bob).communication_preferences.preferred_channel = telegram` and Bob's `telegram_id` from `ResourceNode(Bob).identities`.
4. Outbound dispatches via Telegram → creates `CheckinNode { task_id, recipient=Bob, channel=telegram, thread_id, direction=out }`. Sets Redis reply key `checkin:telegram:{thread_id}:{msg_id} → {user_id=User-1, task_id=TSK-123, counterparty=Bob}`.
5. Persists message to `{user_id_1}/conversations/{Bob}/telegram/{thread_id}.jsonl` AND appends to `TSK-123.intelligence`.
6. Bob replies on the same Telegram thread.
7. Telegram inbound webhook fires → inbound agent classifies sender via `(channel=telegram, sender_telegram_id) → ResourceNode(Bob)`. Tier-1 reply-key match returns `{user_id=User-1, task_id=TSK-123, counterparty=Bob}` → **Route = `counterparty_reply`**.
8. Inbound agent appends to `{user_id_1}/conversations/{Bob}/telegram/{thread_id}.jsonl`, updates `TSK-123.intelligence`, optionally writes a `memory_note` to `{user_id_1}/agents/{user_id_1}/memory/working/context.md` tagged with `counterparty=Bob`.
9. Decision: should Angela respond directly, or surface to User-1?
   - If reply contains a clear status signal (DONE/IN_PROGRESS) → status update flow + intelligence-only; surface in feed.
   - If reply asks a question Angela can answer from context → wake comms agent in `counterparty_conversation` mode → Angela drafts a reply → outbound agent sends via the same thread.
   - If reply needs User-1's input → escalate notification to User-1 (cockpit + their own preferred channel).

**Scenario B — inbound-initiated (Bob → Angela proactively)**
1. Bob proactively messages Angela's Telegram bot: "I'm going to be late on TSK-123 — can we push to Friday?"
2. Inbound webhook fires. No Redis reply key matches the thread.
3. Sender classification:
   - `bot_id / receiving_account_id` (the Telegram bot, the inbound email address, the WhatsApp business number) → mapped to **owning user** via `AgentChannelIdentity` registry → `user_id = User-1`.
   - `sender_telegram_id` → resolved against User-1's graph → `ResourceNode(Bob)`.
   - **Route = `counterparty_proactive`** with `(user_id=User-1, counterparty=Bob)`.
4. Task resolution falls through Tier-2 (TaskID regex match: `TSK-123`) or Tier-3 (vector search restricted to tasks where `Bob` is assignee/resource).
5. Persist message to `{user_id_1}/conversations/{Bob}/telegram/{thread_id}.jsonl`, append to `TSK-123.intelligence`.
6. Wake comms agent in `counterparty_conversation` mode with the loaded task context. Angela can:
   - reply directly via outbound on the same thread (e.g., acknowledgement), OR
   - propose a graph mutation (deadline change) and either auto-apply (if within owner's pre-authorised policy) or surface to User-1 for approval.

### 9.2 Storage layout

Replace the current `{user_id}/chat/history.json` with a counterparty-scoped layout:

```
{user_id_1}/                                          # owner of the agent
  agents/{user_id_1}/
    profile.md
    memory/
      working/context.md                              # cross-counterparty memory
      episodic/…
      semantic/…
  conversations/
    index.json                                        # counterparty list + last activity
    {user_id_1}/                                      # owner-self conversations (cockpit, owner's own WA/TG/email)
      cockpit/{thread_id}.jsonl
      telegram/{thread_id}.jsonl
      email/{thread_id}.jsonl
    {counterparty_id_Bob}/                            # counterparty conversations
      telegram/{thread_id}.jsonl
      email/{thread_id}.jsonl
    {counterparty_id_Carol}/
      ...
```

Conventions:
- One `.jsonl` per `(counterparty, channel, thread_id)`. Append-only.
- Each entry: `{ message_id, ts, direction: in|out, channel, thread_id, sender_id, content, task_refs?, checkin_id? }`.
- `index.json` is a small lookup of counterparty_id → last_activity_at, last_thread_id per channel — used by the comms agent to decide what context to load.
- Owner-self conversations live under `conversations/{user_id_1}/` so the layout is uniform; no special-case path for "user's own chat".

The comms agent loads (a) cross-counterparty `working/context.md` always, plus (b) the relevant counterparty's recent threads only when in `counterparty_conversation` mode — keeping context windows tight while preserving cross-context memory.

### 9.3 Routing decision matrix (inbound)

| Sender match | Reply-key match | Receiving account → owner | Route |
|---|---|---|---|
| Owner's own identity | n/a | yes | `user_chat` → comms agent (owner) |
| Known counterparty | yes | yes | `counterparty_reply` → intelligence + optional comms-agent wake |
| Known counterparty | no | yes | `counterparty_proactive` → comms agent in `counterparty_conversation` mode |
| Unknown sender | no | yes | `unknown_party` → escalate to owner (cockpit notification + preferred channel) |
| Any | n/a | **no** (account doesn't map to any user) | drop / log / dead-letter |

### 9.4 Agent-channel-identity registry

For routing to know *which user owns a receiving Telegram bot / email mailbox / WhatsApp number*, add an `AgentChannelIdentity` table (or storage doc):

```
{ user_id, channel, account_id, display_name, credentials_ref, active }
```

E.g., `{ user_id: User-1, channel: telegram, account_id: @AngelaBot, ... }`. The inbound webhook router maps the receiving `bot_id`/`mailbox`/`number` → `user_id` using this table before any further classification.

For MVP without per-user bots: a single shared bot can route via deep-link `start` parameters carrying `user_id` (Telegram), or `+ext` aliases on email (e.g., `agent+user1@…`). Document both modes; pick per-user identities for production.

### 9.5 New gaps (append to §4 / §8.6)

- **Gap L — Counterparty-scoped conversation storage missing.** Only `{user_id}/chat/history.json` exists today. Introduce `{user_id}/conversations/{counterparty_id}/{channel}/{thread_id}.jsonl` and migrate.
- **Gap M — No counterparty resolution from channel sender id.** Add `(channel, sender_external_id) → ResourceNode|UserNode` index on resource/user identities; reuse `UserNode.identities` (Gap K) and add the equivalent `ResourceNode.identities`.
- **Gap N — No `AgentChannelIdentity` registry.** Inbound router cannot determine the owning user from a receiving account. Add the table + a startup loader.
- **Gap O — `CheckinNode` lacks `recipient_id`, `channel`, `thread_id`, `direction`.** Required for reliable reply linking and conversation persistence. Migration needed.
- **Gap P — Inbound router has no `counterparty_reply` / `counterparty_proactive` / `unknown_party` routes.** Add them per §9.3 matrix.
- **Gap Q — Comms agent has no `counterparty_conversation` mode.** Today its system prompt assumes the partner is the owner. Add a mode flag + prompt variant that constrains what the agent may do on the owner's behalf (auto-reply scope, mutation pre-authorisations).
- **Gap R — No autonomy policy for counterparty replies.** Owner needs to configure what Angela may do unsupervised (acknowledge, accept deadline change ≤ N days, reschedule, escalate). Stored as **MinIO `.md` policy files** under the agent (per §9.7), not hardcoded.

### 9.7 Policies as MinIO `.md` files (NOT hardcoded)

All per-user policy guidance follows the **same MinIO/markdown pattern as `profile.md`, `working/context.md`, episodic/semantic memory** — never hardcoded, never structured-only on graph nodes. This keeps policies editable via the Intelligence Hub (PRD 15) and consistent with how the rest of the agent's "cognitive state" is authored.

Important distinction:

- **System-level knowledge** (graph rules, scoring rules, edge-creation rules) — ships in code under [`src/graphclaw/gateway/prompts/knowledge/*.md`](../../../Projects/graphclaw/src/graphclaw/gateway/prompts/knowledge/), loaded via the existing `read_knowledge` tool. **Same as today.**
- **Per-user policies** (delegation, counterparty etiquette, escalation, reply tone) — live in MinIO under `{user_id}/agents/{agent_id}/policies/*.md`. Loaded into the agent's system prompt at turn time, Redis-cached 15min (same TTL as profile.md). Editable via Intelligence Hub.

**New `StoragePaths` entries:**

```
{user_id}/agents/{agent_id}/policies/delegation.md
{user_id}/agents/{agent_id}/policies/counterparty_etiquette.md
{user_id}/agents/{agent_id}/policies/escalation.md
{user_id}/agents/{agent_id}/policies/reply_tone.md
```

**File format — YAML frontmatter (hard limits) + markdown body (narrative guidance):**

```markdown
---
# Hard limits — programmatically enforced before LLM sees them
auto_acknowledge: true
accept_deadline_extension_max_days: 3       # 0 = always escalate
allowed_state_transitions:
  - { from: WAITING, to: IN_PROGRESS }
  - { from: IN_PROGRESS, to: BLOCKED }
escalate_on_blocker: true
escalate_on_unknown_topic: true
recipient_overrides:
  CEO-001: { accept_deadline_extension_max_days: 0 }   # always escalate for CEO
---

# Delegation Policy

When responding to counterparties on my behalf, prefer concise acknowledgements.
For deadline extensions within the limit, accept and reschedule the task; otherwise
escalate to me with the reason summarized.

For status updates from Bob (engineer), capture his estimate but do not mutate
the deadline without confirming with me first — Bob tends to under-estimate.
```

**Why hybrid:**
- The **frontmatter** is parsed and used by a small policy-evaluator BEFORE the LLM is invoked — guarantees Angela can't accept a 30-day deadline extension regardless of how a counterparty phrases it.
- The **markdown body** is injected into the system prompt during `counterparty_conversation` mode so the LLM applies tone, recipient-specific nuance, and soft judgement.
- Both are user-authored via Intelligence Hub — same edit flow as `profile.md`.

**Loading:**
- Reuses the existing `_build_system_prompt` path in [main_orchestrator.py:1101](../../../Projects/graphclaw/src/graphclaw/agent/main_orchestrator.py#L1101) — add a `policies` block injection when mode is `counterparty_conversation`.
- Frontmatter parsed by the policy-evaluator (a tiny new module — pyyaml + Pydantic schema), cached in Redis alongside the prompt.
- No new tool needed for read; can reuse `read_knowledge` semantics if exposed to the agent for self-inspection ("what are my current policies for Bob?").

**Implication for Gap R:** the field on `UserNode.preferences` is removed from the design — `delegation_policy` is **a file, not a node attribute**. The graph stays clean; policy authorship stays in the Intelligence Hub.

**Cockpit Intelligence Hub (PRD 15) addition:** add a new left-nav item **Policies** alongside Agent Profile / Memory / Skill Author, with one editor per policy file (delegation, counterparty etiquette, escalation, reply tone). YAML frontmatter is rendered as a structured form on top + raw editor underneath, body is a markdown editor.

### 9.6 Worked-example coverage (append to §6 verification)

- **Outbound-then-reply**: simulate Scenario A end-to-end → assert (a) `{user_id_1}/conversations/{Bob}/telegram/{thread_id}.jsonl` has both messages, (b) `TSK-123.intelligence` has both lines, (c) owner's own chat log is untouched.
- **Counterparty-proactive**: simulate Scenario B → assert correct route classification, assert task resolved via Tier-2/3, assert comms agent is woken in `counterparty_conversation` mode and replies on same thread.
- **Cross-contamination test**: send a counterparty message → assert nothing is written to `{user_id_1}/conversations/{user_id_1}/...` (owner-self log).
- **Receiving-account routing**: same Telegram message arriving via two different bots (mapped to two owners) → assert each lands in the correct owner's substrate.

---

---

## 9.8 User identity, onboarding, and cross-tenant resolution

This is a separate concern from the comms triad — it's about *which UserNode/ResourceNode the orchestrator is talking about*, how those nodes come to exist, and how multi-tenant discovery works. Today's gaps here are larger than they first appear; some pieces exist and just need to be connected, others must be built fresh.

### 9.8.1 What already exists

| Piece | File | Purpose |
|---|---|---|
| `UserProvisioningService` | [auth/provisioning.py](../../../Projects/graphclaw/src/graphclaw/auth/provisioning.py) | On first OAuth login, atomically creates `UserNode` + S3 prefix + `WorkspaceNode` + JWTs. Idempotent by `oauth_subject`/email. |
| `AliasResolver` (channel-identity) | [gateway/alias_resolver.py](../../../Projects/graphclaw/src/graphclaw/gateway/alias_resolver.py) | Redis-backed `(channel, sender_id) → user_id` mapping. **Solves Gap M for inbound routing**, but is **not** a name-alias resolver. |
| `WorkspaceNode` | inferred from provisioning.py | Org/tenancy boundary candidate — natural home for an org-scoped user directory. |

### 9.8.2 What's missing — and why each matters

#### Onboarding conversation (no agent-led first-run experience)
`UserProvisioningService` creates the `UserNode` shell, but the user's first interaction with their orchestrator goes straight into the normal chat loop with an essentially empty profile. There is no:
- Detection of "first turn ever" vs returning user.
- Welcome / persona-elicitation conversation (which would write `profile.md`).
- Channel-identity collection (Telegram chat ID, WhatsApp number, additional emails).
- Working-hours / preferences / `delegation_policy` initial setup.
- Marker that onboarding has completed.

#### Name-aliases on people (separate from channel identities)
`UserNode` has `name`, `email`. `ResourceNode` has `name`, `contact`. **Neither has an `aliases: list[str]` field.** When User-1 says "delegate to Bob", there's no place to remember that User-1's "Bob" maps to `RES-engineer-bob-007`. Today the orchestrator would have to guess the same person each time.

This is *different from* `UserNode.identities` (Gap K, which is about channel addresses like phone/telegram) — aliases are the **owner's nicknames** for the same entity.

#### Cross-tenant shadow references
A multi-tenant org has Carol as her own `UserNode` (with her own agent and substrate). When User-1 wants to delegate to Carol, the right shape is:
- A `ResourceNode` shadow in User-1's graph with `linked_user_id = USER-carol-…`.
- Owner-specific data (User-1's alias for Carol, User-1's notes about her) lives on the shadow.
- Canonical data (Carol's preferred channel, identities, working hours) is **read through** to Carol's actual `UserNode` so updates by Carol are seen by everyone who linked to her.

`ResourceNode.linked_user_id` does not exist today.

#### Org-level user directory
For User-1's orchestrator to *discover* Carol when she isn't in User-1's graph, an **org-scoped directory** is needed. **The boundary already exists in the model — see §9.8.2.5 for the OrganizationNode / WorkspaceNode finding** — what's missing is the indexed lookup surface and the API.

Recommend a **Postgres index** keyed on `org_id`: row per user with `user_id, org_id, display_name, email, identities, discoverable_aliases, visibility_policy, last_updated`. Indexed for fuzzy text search (trigram + embedding). Updated whenever a user edits their profile or `OrganizationNode.members` changes. Decoupled from AGE — same pattern Redis uses today for fast lookup over slow primary storage.

Why Postgres and not a shared org-graph in AGE: cheaper, simpler, no cross-tenant ACL complexity inside the graph engine, and the search workload (fuzzy text, identity matching) is a poor fit for Cypher anyway. AGE stays per-user.

#### 9.8.2.5 OrganizationNode / WorkspaceNode — the original intent (already correct)

You asked whether `WorkspaceNode` is the right boundary or whether to add `OrgNode`. **Both exist already** in [models/nodes.py](../../../Projects/graphclaw/src/graphclaw/models/nodes.py) and the design is correctly two-tier:

**OrganizationNode** (`ORG-{uuid}`, [nodes.py:588](../../../Projects/graphclaw/src/graphclaw/models/nodes.py#L588)) — the **tenant / sphere boundary**:
```
name, domain (e.g. "acme.com" for SSO matching),
owner_id (founding user), members: list[OrgMember],
settings: OrgSettings
```
Docstring: *"multi-user organization workspace boundary. Organizations own one or more Workspaces and hold the membership list that determines who can see and act on tasks within those workspaces."*

**WorkspaceNode** (`WS-{uuid}`, [nodes.py:614](../../../Projects/graphclaw/src/graphclaw/models/nodes.py#L614)) — a **project-grouping inside an org**:
```
org_id (parent), name, description, visibility,
task_prefix, member_ids (subset of org members), is_default
```
Docstring: *"scoped collection of tasks and goals... All tasks/goals that are SCOPED_TO_WS this node are only visible to workspace members."*

Confirmed by adjacent code:
- [api/admin/members.py](../../../Projects/graphclaw/src/graphclaw/api/admin/members.py) — admin member management is **org-scoped**: *"Member records are stored on the OrganizationNode in the graph store"*.
- [cockpit/docs/prd/02-graph-cockpit.md:151](docs/prd/02-graph-cockpit.md) — *"Top-level selector in the header to switch between organization workspaces (Personal, Work, Side Project, etc.)"* — multiple workspaces per org.
- [cockpit/docs/prd/05-settings-panel.md](docs/prd/05-settings-panel.md) — `/settings/organizations` API surface for org CRUD.

**This maps cleanly to both deployment models:**

| Deployment | OrganizationNode | WorkspaceNode |
|---|---|---|
| **On-prem per-org** (current intent) | One per deployment ("Acme"). All employees join as members. Domain `acme.com` drives SSO. | Many per org — Engineering, Marketing, Project-X. Tasks scoped here. |
| **SaaS (future)** | Many — each user-created "sphere" is an `OrganizationNode`. Users can belong to multiple orgs. `domain` may be unused; invitation-driven membership. | Many per org — same as on-prem. |

**Decision: keep both, don't add new types.** The original design intent is correct. The §9.8 design plugs into it as follows:

- **Org-level user directory** → indexed by `org_id`. Membership comes from `OrganizationNode.members`.
- **Org-level task index** (Gap AA, approach A.1) → indexed by `org_id`, with `workspace_id` as a finer filter.
- **Directory visibility / consent policy** → on `OrganizationNode.settings` (org-wide default) + per-user override on `UserNode.preferences.discoverability`.
- **Cross-org users in SaaS** (a user in multiple orgs): `resolve_user` scopes its directory query to **orgs that BOTH the calling user and the candidate share**. A user not in any of User-1's orgs is invisible to User-1's resolution.
- **Workspace-scoped delegation**: when User-1 delegates inside `WS-engineering`, prefer candidates who are workspace members; fall back to org members; never resolve to users outside the org.

The **only `OrganizationNode` extension** §9.8 needs is:
- `settings.directory_visibility: OrgDirectoryVisibility` (`open` | `name-only` | `consent-required` | `invitation-only`) — default `open`.

No new node types are required.

#### `resolve_user` tool with confidence + clarify-with-user
There is no orchestrator tool that takes a free-form query like "Bob" and returns ranked candidates with confidence so the orchestrator can ask "Did you mean Bob Smith (engineering) or Bob Lee (sales)?". Required for the alias-resolution behaviour you described.

#### Interactive `create_person_via_dialog` flow
When resolution fails entirely, the orchestrator should walk the user through field collection (name, role, channel preference, contact info) and create the resource node — without making the user invoke a tool form themselves.

#### Cross-tenant privacy / consent policy
Linking to another user's `UserNode` exposes their preferences and identities. Need an org-level setting (e.g., on `WorkspaceNode` or org config):
- `directory_visibility`: `open` | `name-only` | `consent-required` | `invitation-only`
- Per-user override on `UserNode.preferences.discoverability`.

### 9.8.3 Resolution algorithm (delegation flow)

When the orchestrator hears "delegate this to <name>":

```
1. Local alias hit
   → Search current user's ResourceNodes/UserNode aliases for exact match.
   → If 1 match, use it.

2. Local fuzzy match
   → Trigram/embedding similarity over local nodes' name+aliases.
   → If top candidate ≥ high-confidence threshold and gap to #2 is large, propose+confirm.
   → If multiple plausible, list top N for user to pick.

3. Org directory lookup
   → Query Postgres org index for fuzzy match across the workspace.
   → If matches found:
       a. Filter by visibility policy.
       b. Confirm with user: "I see Carol Martinez in our org — link her?"
       c. On confirm: create ResourceNode shadow with linked_user_id, copy
          discoverable identities, add user's chosen alias to shadow.

4. New external person
   → No match anywhere. Walk the user through create_person_via_dialog:
       name → role → primary channel → contact → optional aliases.
   → Create ResourceNode (no linked_user_id), persist alias.

5. Alias drift
   → If resolution succeeded but the alias used wasn't on the matched node,
     append it to the node's aliases list (with provenance: "added by USER-1
     on <date> from chat").
```

### 9.8.4 Onboarding state machine (first-run experience)

Triggered from `process_chat_message` (or the channel-agnostic equivalent) when `profile.md` is missing or carries `onboarding_complete: false` in frontmatter.

```
States:
  WELCOME → PERSONA → CHANNELS → WORKING_HOURS → PREFERENCES → POLICIES → DONE

Per-state behavior:
  WELCOME       — short greeting, set expectations, ask name confirmation.
  PERSONA       — "How would you describe your role / what do you want me to
                  optimise for?" → write to profile.md body.
  CHANNELS      — show channels currently known (email from auth) and ask for
                  Telegram / WhatsApp / additional emails. Each channel that
                  the user provides is stored on UserNode.identities and (for
                  inbound routing) registered with AliasResolver.
  WORKING_HOURS — start/end + timezone confirmation (auto-detect).
  PREFERENCES   — briefing time, follow-up cadence, interrupt threshold.
  POLICIES      — offer to seed delegation.md / counterparty_etiquette.md from
                  templates; user can edit later via Intelligence Hub.
  DONE          — set profile.md frontmatter `onboarding_complete: true`,
                  trigger a graph snapshot, hand control back to normal loop.
```

The state machine lives in the orchestrator itself, NOT a separate FSM service — each "state" is just a system-prompt variant with a tool whitelist (e.g., `WELCOME` only allows `set_user_name`; `CHANNELS` only allows `add_user_identity`). User can resume mid-onboarding (state persisted in profile.md frontmatter).

This same pattern is reused for the **interactive resource creation** flow — same FSM mechanism, different states (NAME → ROLE → CHANNEL → CONTACT → ALIASES → DONE).

### 9.8.5 New gaps (append to §4 / §8.6 / §9.5)

- **Gap S — No agent-led onboarding flow.** Provisioning creates the shell; the orchestrator never welcomes the user, never collects persona/channels/preferences. Build the FSM-as-prompt-variants per §9.8.4.
- **Gap T — No `aliases` field on `UserNode` and `ResourceNode`.** Required for owner-specific name memory.
- **Gap U — No `linked_user_id` on `ResourceNode`.** Required for cross-tenant shadow references.
- **Gap V — No org-level user directory.** Build a Postgres index keyed on `workspace_id`/`org_id`, fed by user-profile changes, queried by `resolve_user`.
- **Gap W — No `resolve_user(query, hints?)` tool.** Returns ranked candidates `{ node_id, source: local|org, confidence, reason }`. Orchestrator handles confirm-with-user.
- **Gap X — No `create_person_via_dialog` flow** (FSM reused from onboarding).
- **Gap Y — No directory visibility / consent policy.** Add `WorkspaceNode.directory_visibility` and `UserNode.preferences.discoverability`. Default `open` within an org.
- **Gap Z — Alias-drift autoload.** When resolution succeeds via fuzzy match, automatically register the new alias on the matched node (with provenance).

### 9.8.6 Alignment with current GraphClaw philosophy

The current implementation already commits to:
- Per-user MinIO partitioning (multi-tenant isolation by `user_id` prefix).
- Per-user agent identity (`agent_id == user_id`).
- Channel-identity resolution via Redis (`AliasResolver`).
- Atomic provisioning with rollback (`UserProvisioningService`).
- **Two-tier tenancy**: `OrganizationNode` (sphere/tenant boundary, members + SSO domain + settings) and `WorkspaceNode` (project-grouping inside an org). Already in [models/nodes.py](../../../Projects/graphclaw/src/graphclaw/models/nodes.py) — see §9.8.2.5.

The §9.8 design **stays inside this philosophy**:
- Onboarding writes to the same `profile.md` the Intelligence Hub already governs — no new memory tier.
- Cross-tenant references use the existing node graph (`ResourceNode` with a new `linked_user_id` field) — no new entity type.
- Aliases live on the nodes — no separate alias service.
- Org directory + org task index are Postgres tables alongside MinIO — same pattern as Redis is used today for fast lookup over slow primary storage.
- Per-user policies (Gap R, §9.7) extend cleanly to discoverability; org-wide visibility lives on `OrganizationNode.settings`.
- Cross-tenant task visibility (Gap AA, A.1) reuses the existing event bus to keep the org task index in sync; no graph-level cross-tenant ACL is introduced.

**No new node types are required.** The original GraphClaw two-tier tenancy model (Org owns Workspaces) is the right shape for both the on-prem-per-org deployment and the future SaaS multi-org model — confirmed by `OrganizationNode.domain` for SSO, `OrganizationNode.members` for membership, and the existing admin/settings endpoints. The earlier "open question on WorkspaceNode-as-org" is closed — no schema change needed at the tenancy layer.

### 9.8.7 Validation walkthroughs — three delegation scenarios

These walk the §9.8.3 resolution algorithm end-to-end. Two scenarios pass the design unchanged; the third surfaces real gaps (AA/AB/AC) that the design needs to absorb.

#### Scenario 1 — Bob does not exist anywhere

> User-1: "Assign TSK-X to Bob."

| Step | What happens |
|---|---|
| 1 | Orchestrator calls `resolve_user("Bob")`. |
| 2 | Local hit / fuzzy → no match in User-1's graph. |
| 3 | Org-directory query → no match in the workspace's Postgres index. |
| 4 | Falls through to **§9.8.3 step 4** (new external person). Orchestrator: "I don't see Bob anywhere. Add him as a new contact?" |
| 5 | On confirm → enter `create_person_via_dialog` FSM (Gap X) — collects name, role, preferred channel, contact, aliases. |
| 6 | New `ResourceNode` created with `aliases: ["Bob"]`, no `linked_user_id`. |
| 7 | `ASSIGNED_TO` edge from TSK-X. |
| 8 | Outbound agent (if intent implies it) sends a notification on the chosen channel; CheckinNode + intelligence write per §8 / §9. |
| 9 | Distillation post-step writes `TSK-X.intelligence` + `working/context.md`. |

**Verdict: design holds end-to-end.** One small refinement: the dialog should ask "Is Bob likely to onboard to GraphClaw later?" — if yes, mark the ResourceNode as *upgradeable* so when a UserNode appears later that matches Bob's identities, the shadow can be auto-linked (related to Gap Z, in reverse).

#### Scenario 2 — Bob is already User-2 in the org

> User-1: "Assign TSK-X to Bob."

| Step | What happens |
|---|---|
| 1 | `resolve_user("Bob")` → local: no match. |
| 2 | Org-directory query → returns USER-bob-002 (display_name "Bob Smith"). |
| 3 | Orchestrator confirms: "I see Bob Smith (engineering) in our org — link him?" |
| 4 | On confirm → create `ResourceNode` shadow `RES-bob-{uuid}` with `linked_user_id: USER-bob-002`, `aliases: ["Bob"]`, copy discoverable identities. |
| 5 | `ASSIGNED_TO` edge from TSK-X to the shadow. |
| 6 | Outbound: recipient = shadow → read-through to USER-bob-002 → preferred channel = telegram → dispatch to Bob's real telegram_id. |
| 7 | Bob replies. Inbound classifier: receiving bot → owner User-1; sender telegram_id → matches USER-bob-002 (via `AliasResolver`); thread → `counterparty_reply` route. Persisted under `{User-1}/conversations/{shadow_id}/telegram/{thread}.jsonl`. |
| 8 | Optional notification: per Bob's `discoverability` + notification settings, his own agent (Brian) is informed that USER-1 assigned him a task. |

**Verdict: design holds for the message flow.** But the walkthrough surfaces a real new question:

> **Where does TSK-X live so that Bob's own agent (Brian) can see "tasks assigned to me by others" when Bob asks Brian for a daily briefing?**

Today TSK-X exists only in User-1's graph. The shadow `ResourceNode` is User-1's view; it does not project the task into User-2's substrate. So Brian (Bob's agent) can't natively see TSK-X. **This is a real gap not yet covered by the design — see Gap AA below.**

#### Scenario 3 — Same person, different alias over time

> Earlier: User-1 said "Assign TSK-Y to Mr. Smith" (no Bob in org; created `RES-mrsmith-001` with `aliases: ["Mr. Smith"]`).
> Today: User-1 says "Assign TSK-Z to Bob."

What today's design does:

| Step | What happens |
|---|---|
| 1 | `resolve_user("Bob")` → local: no alias match for "Bob"; fuzzy "Bob" vs "Mr. Smith" → very low similarity. |
| 2 | Org-directory query → no match (assume Bob isn't onboarded). |
| 3 | Falls through to "new external person". Creates `RES-bob-002`. |
| 4 | TSK-Z assigned to `RES-bob-002`. |

**Result: the system now believes Mr. Smith and Bob are two different people. They are not.** The daily briefing would report:
- "Tasks for Mr. Smith: TSK-Y"
- "Tasks for Bob: TSK-Z"

Which is wrong from User-1's mental model. **This is the third gap surfaced by validation — see Gap AB below.**

The right behaviour, after the design is patched:

| Step | What should happen |
|---|---|
| 1 | `resolve_user("Bob")` → no exact match. |
| 2 | Before falling to "new external person", **the create-flow's first prompt offers the top-N existing local resources**: "I don't know Bob. Is this someone new, or do you mean one of: Mr. Smith, Anita Cohen, Carlos Reyes?" |
| 3 | User-1 picks "Mr. Smith". |
| 4 | Orchestrator appends `"Bob"` to `RES-mrsmith-001.aliases` (alias-drift autoload, Gap Z, with provenance). |
| 5 | TSK-Z assigned to `RES-mrsmith-001`. |
| 6 | Optional: orchestrator asks "Want me to make Bob the canonical name going forward?" — if yes, swap `name` ↔ alias. |

And if the duplicate has already been created in a prior session before this fix landed, the orchestrator (or the user) can call a `merge_resource(node_a, node_b)` tool — see Gap AB.

**Briefing rendering after the fix:**
- Briefing groups by entity (`node_id`), not by alias string.
- Renders canonical `name` with parenthetical aliases when there's >1: "Bob (also: Mr. Smith) — TSK-Y, TSK-Z".
- If the orchestrator detects suspected duplicates that haven't been merged, the briefing flags them: "Heads up — *Mr. Smith* and *Bob Jenkins* look similar; same person? Merge?"

### 9.8.8 New gaps surfaced by validation (append to §9.5)

- **Gap AA — Cross-tenant task projection (DECIDED: approach A.1).** When User-1 assigns a task to a `ResourceNode` shadow whose `linked_user_id` points to USER-2, the task is invisible to User-2's own agent (Brian) by default. The chosen approach:

  **A.1 — Read-through query model (single source of truth):**
  - TSK-X stays in **User-1's graph** as the canonical record. No mirror.
  - **Org-level task index** (Postgres, sibling of the user directory): row per `(task_id, owner_user_id, org_id, workspace_id, [assignee.linked_user_id, ...], state, deadline, last_activity_at, summary_text)`. Updated on task create/update/state-transition via the existing event bus. Indexed by `assignee.linked_user_id` for fast lookup.
  - Brian gets a new core tool `list_external_assignments_for_me(filters?)` that queries the org task index for rows where any `assignee.linked_user_id == USER-bob-002`, scoped to orgs Bob is a member of.
  - Brian also gets `get_external_task_summary(task_id)` — returns the redacted summary projection (title, deadline, owner, state, last update). For full detail, Brian's UI offers "request access" → owner approves → owner's `delegation_policy` may pre-authorise.
  - **Cross-tenant ACL** lives at the API/repository layer, not in AGE. State mutations on a task you don't own are gated through:
    1. Owner's `delegation_policy` allow-list (e.g., assignee may move WAITING → IN_PROGRESS).
    2. Otherwise → request flows back to the owner's comms agent as a `counterparty_proactive` style message.
  - **Briefing on Bob's side**: Brian's daily briefing aggregator unions local tasks + `list_external_assignments_for_me()`. External tasks are visually distinguished ("assigned by USER-1") and grouped under a separate section.
  - **Privacy**: only `assignee.linked_user_id`, redacted summary, and minimum metadata cross the tenant boundary. Full task body and comments stay in the owner's substrate. Cross-org leakage prevented because the index is scoped by `org_id` and queries are scoped to orgs the requester is a member of.
  - **A.2 (mirrored task)** is rejected for now — kept as a documented fallback for future offline-tolerant or privacy-isolated cases (e.g., regulated tenants that prohibit cross-tenant queries).
  - Adds to wave plan: **org task index schema + indexer + read API + Brian's briefing extension + ACL layer**.

- **Gap AB — Entity disambiguation at create time + merge tool.**
  - At create time: the `create_person_via_dialog` FSM's first state must offer top-N local candidates (fuzzy by `name + aliases`) and ask "new, or one of these?" before falling to "new external person". This catches the Mr. Smith / Bob duplicate at the moment of friction.
  - For already-duplicated entities: add a `merge_resource(keep_id, merge_id, canonical_name?)` tool. Behaviour: redirect all edges from `merge_id` → `keep_id`; concatenate `aliases`; concatenate `intelligence` chronologically; concatenate counterparty `conversations/{merge_id}/...` into `conversations/{keep_id}/...`; archive `merge_id` with a tombstone redirect. Surface in the cockpit as a "Merge contacts" action.

- **Gap AC — Briefing must group by entity and render canonical-with-aliases.**
  - Briefing aggregator groups task lists by `assignee.node_id` (not by displayed name), then renders `canonical_name` with parenthetical aliases when the entity has >1 alias used in the briefing window.
  - Briefing also runs a duplicate-suspicion pass over recently-touched ResourceNodes (fuzzy across `name + aliases + identities`) and surfaces "possible duplicates — merge?" prompts.

### 9.8.9 Updated wave plan / requirements (append to §10 build plan)

- Wave 7 expands to include **AB** (disambiguation prompt + `merge_resource` tool) and **AC** (briefing entity-grouping + duplicate-suspicion pass).
- New **Wave 8.5** — **Gap AA** (cross-tenant task projection): org-level task index, `list_external_assignments_for_me`, cross-tenant ACL on state mutations, Brian's briefing extension.
- Requirements doc gains FR-IDs:
  - FR-ID-019..023 — disambiguation, merge tool, alias-drift, briefing rendering, duplicate-suspicion pass
  - FR-XU-007..010 — cross-tenant task projection model, ACL, external-assignment query, briefing on the assignee side

### 9.8.10 Net design verdict

The §9.8 design **holds for Scenarios 1 and 2** without changes (Scenario 1 already covered; Scenario 2 covered for messaging, with Gap AA needed for cross-tenant task visibility — an extension, not a contradiction).

The design **must be patched** for Scenario 3: add disambiguation prompt at create time (Gap AB), a merge tool (Gap AB), and entity-grouped briefing rendering (Gap AC). All three are additive and stay inside the existing philosophy (nodes + aliases + per-user agent + Intelligence Hub authorship). No model rewrites required.

### 9.8.12 Stress-test scenarios — design resiliency check

Each scenario walks the §8 / §9 design end-to-end. Verdict legend:
**✅ Holds** = design covers it as written.
**⚠️ Patch** = design needs a small additive fix (see Gaps AD–AM).
**❌ Open** = real architectural decision still owed.

---

#### Lifecycle

**ST-01 — Bob (User-2) leaves the org mid-task**
Bob has TSK-X assigned (linked from User-1's `ResourceNode` shadow). Bob is removed from `OrganizationNode.members`.
Trace: org task-index entry's `assignee.linked_user_id` is still USER-2, but cross-tenant ACL filter `assignee_user IN org.members` now drops it from Brian's `list_external_assignments_for_me()`. User-1's view is unchanged (canonical task). **But:** the shadow's `linked_user_id` still points at a node Brian can no longer be reached through; outbound to Bob's preferred channel may still work (channel identity lives on Bob's UserNode), but should it? And Brian no longer sees TSK-X — the work doesn't get done.
Verdict: ⚠️ Patch — **Gap AD (counterparty detachment)** + **Gap AK (membership-change cascade)**.

**ST-02 — Bob deletes his account entirely**
USER-bob-002 is deprovisioned. `linked_user_id` on User-1's shadow now dangles. Read-through preference lookups 404.
Trace: shadow needs to "freeze" — copy last-known canonical preferences/identities onto the shadow, set `linked_user_id = null`, set `link_status = detached_user_deleted`. Outbound continues to use the frozen identities (or surfaces a one-time prompt: "Bob's account was removed — keep his last known contact info, or unassign?").
Verdict: ⚠️ Patch — **Gap AD**.

**ST-03 — Returning user with pre-existing profile.md (no onboarding frontmatter)**
Existing user logs in after the onboarding FSM ships. `profile.md` exists, no `onboarding_complete` field.
Trace: default to `onboarding_complete: true` to avoid re-onboarding everyone. Add a one-time migration to write the frontmatter on next load.
Verdict: ✅ Holds with explicit default — captured in requirements doc migration plan.

**ST-04 — Onboarding abandoned mid-flow**
User quits during PERSONA state. Returns next day.
Trace: profile.md frontmatter has `state: PERSONA, onboarding_complete: false`. Orchestrator resumes from PERSONA on next chat turn. Other tools blocked by FSM tool allow-list until DONE.
Verdict: ✅ Holds — design already specifies resumability.

---

#### Concurrency & consistency

**ST-05 — Bob fires 3 Telegram messages in 5 seconds**
Inbound webhook fires three handlers concurrently.
Trace: each handler tries to append to `{User-1}/conversations/{Bob}/telegram/{thread}.jsonl` and to `{User-1}/agents/{User-1}/memory/working/context.md`. Existing `_memory_lock` guards working/context.md, but conversation file has no lock today. Append-order can scramble; intelligence-line ordering can interleave.
Verdict: ⚠️ Patch — **Gap AJ (per-file write locks for shared MinIO state)**.

**ST-06 — Distillation post-step partially fails**
Orchestrator returns chat reply to user. `update_node_intelligence` throws (Memgraph blip), but `working/context.md` write already succeeded.
Trace: state is inconsistent — memory has a note about TSK-X, node has no log entry. User sees reply (good) but next briefing under-counts TSK-X activity.
Verdict: ⚠️ Patch — **Gap AF (distillation idempotency + retry queue)**: post-step writes go through a small outbox table; on failure, retry with idempotency key derived from `(message_id, target)`.

**ST-07 — MinIO/Redis outage during a chat turn**
Profile.md cache miss + MinIO down → cannot load profile or policies.
Trace: orchestrator should **fail closed for counterparty_conversation** (cannot reason about delegation policy → refuse to send outbound on someone's behalf) but may **degrade gracefully for owner chat** (use system-default profile + log a warning to the user). Today's design doesn't specify either.
Verdict: ⚠️ Patch — **Gap (extends R via §9.7)** with explicit fail-mode policy: structured frontmatter must include `fail_mode: closed|degraded` per policy file.

**ST-08 — Org task index falls behind (event-bus drop)**
Indexer crashes; TSK-X state changes WAITING→DONE but index still shows WAITING. Brian's briefing shows stale.
Trace: needs periodic full-resync. Same pattern as Memgraph snapshot ↔ event log.
Verdict: ⚠️ Patch — **Gap AE (org-task-index reconciliation job)**: nightly full-sync diff against AGE source-of-truth, plus a manual rebuild admin endpoint.

---

#### Identity & disambiguation

**ST-09 — User-1 has Bob Smith AND Bob Lee, says "delegate to Bob"**
Trace: `resolve_user("Bob")` local fuzzy returns 2 candidates with similar scores. Per §9.8.3 step 2, orchestrator lists top-N and asks user to pick.
Verdict: ✅ Holds — design already covers ambiguous-match disambiguation.

**ST-10 — Three "John Smith" entries in the org directory**
Trace: org-directory query returns 3 hits, all with similar fuzzy score. Orchestrator presents disambiguation card with discriminators (workspace, role, email domain).
Verdict: ✅ Holds — but *requires* the directory schema to include discriminating fields (workspace memberships, role, email domain). Already captured in §9.8.2 directory schema.

**ST-11 — Bob changes his Telegram handle**
Old `AliasResolver` mapping `(telegram, @oldhandle) → USER-bob-002` is now stale. Bob messages Angela from `@newhandle`. Inbound classifier fails to resolve sender.
Trace: route falls to `unknown_party` despite being a known user. Today there is no recovery — the message would escalate as unknown.
Verdict: ⚠️ Patch — **Gap AG (channel-identity drift recovery)**: when a sender doesn't resolve, attempt fuzzy match by other signals (Telegram display name vs `UserNode.name`, common contacts, message references to known tasks). On candidate match, surface to owner: "I think this is Bob from a new handle — link?". On confirm, register the new mapping.

**ST-12 — Bob sends an email instead of Telegram (his preferred channel)**
Trace: inbound classifier checks `AliasResolver.resolve(email, bob@acme.com)` → known → sender = USER-bob-002. Routes correctly. The fact that channel ≠ preferred_channel doesn't break routing; preference is only for *outbound*.
Verdict: ✅ Holds.

**ST-13 — Comms-agent merges two ResourceNodes mid-conversation**
Angela is in `counterparty_conversation` mode with `RES-mrsmith-001`. User-1 (in cockpit) hits "Merge Bob into Mr. Smith".
Trace: `merge_resource` tool concatenates aliases, intelligence, and conversations chronologically. **But:** Angela's active context (loaded conversation history, prompt-injected aliases) is now stale. Race possible — Angela may attempt to write to the now-archived `RES-bob-002` path.
Verdict: ⚠️ Patch — **`merge_resource` must invalidate orchestrator caches for both nodes** (Redis profile/context cache key per node) and emit an `entity.merged` event. Active counterparty sessions detect via cache invalidation and reload.

---

#### Cross-tenant & multi-org

**ST-14 — Carol is in ORG-A and ORG-B; User-1 only in ORG-A**
User-1 says "delegate to Carol". Org directory query.
Trace: per §9.8 SaaS rule, scope to **orgs the caller is a member of** — only ORG-A. If Carol is in ORG-A, she resolves; the ORG-B membership is invisible. Correct isolation.
Verdict: ✅ Holds.

**ST-15 — User-1 in ORG-A and ORG-C; assigns TSK-X (in ORG-C workspace) to Bob (member of ORG-A only)**
Trace: ResourceNode shadow created in User-1's ORG-C-scoped substrate. Org task index entry has `org_id=ORG-C`. Brian (Bob) queries `list_external_assignments_for_me()` → filter `org_id IN bob.orgs` returns nothing for ORG-C. **Brian doesn't see TSK-X** — Bob is not in ORG-C. Privacy boundary holds. **But:** the task got assigned to a non-member. Should the system have prevented the assignment?
Verdict: ⚠️ Patch — **Gap AL (org-scoped assignment validation)**: at delegation time, refuse `linked_user_id` whose UserNode isn't in the task's org. Surface as: "Bob isn't in ORG-C — invite him first or pick a different person."

**ST-16 — User-1 deletes their account; counterparties have shadows pointing back**
User-1 owned TSK-X; Bob has been responding via Telegram. User-1 is deprovisioned.
Trace: TSK-X is in User-1's substrate — what happens? Cascade delete? Or is org-owned tasks promoted to org? Bob's external-assignment view loses TSK-X. The Telegram thread the outbound created is orphaned (the receiving bot still exists if shared, but the owning user is gone).
Verdict: ❌ Open — **task ownership transfer policy on user deletion** is not specified anywhere. Options: cascade-delete (data loss), transfer to org-owner, transfer to workspace owner, archive in a tombstone bucket. Punts to org admin policy. **Capture as open question in requirements doc.**

**ST-17 — Cross-org assignment by accident (privacy leak attempt)**
Malicious or buggy caller queries org task index without org filter.
Trace: org-task-index ACL must enforce `org_id IN caller.orgs AND (caller.user_id == owner OR caller.user_id IN assignee.linked_user_id[])`. Repository layer, not application layer.
Verdict: ⚠️ Patch — **Gap AL (mandatory ACL filter at repo layer)** — guarantees no application code can bypass it. Same pattern as MinIO `{user_id}/` partitioning.

---

#### Policy & autonomy

**ST-18 — Bob asks for 30-day deadline extension; policy allows max 3**
Trace: Angela in `counterparty_conversation` parses Bob's request, evaluates against frontmatter `accept_deadline_extension_max_days: 3`. Hard limit fails → escalate. Angela replies on Telegram with: "I'll need to confirm this with USER-1 — I'll get back to you." → fires owner-notification on User-1's preferred channel.
Verdict: ✅ Holds — design already specifies frontmatter hard limits + escalation.

**ST-19 — Owner offline when escalation needed**
Angela needs User-1's approval but User-1 hasn't been on cockpit for hours and isn't responding to push.
Trace: today there is no queue. Angela either holds the conversation (Bob waiting) or proceeds with conservative default (no policy says what conservative means).
Verdict: ⚠️ Patch — **Gap AH (owner-offline escalation queue)**: pending-decision queue with timeout + per-policy fallback (`on_owner_unreachable_after_hours: { default: hold, after_24h: escalate_to_workspace_admin }`). Bob gets a holding message; queue surfaces in cockpit's next session.

**ST-20 — A skill/sub-agent wants to message a counterparty directly**
Sub-agent finishes work and wants to email Bob.
Trace: should it go through outbound directly (low overhead) or through comms agent (policy enforcement)? Today's design implies outbound is a peer agent — sub-agent could call it directly. **But** delegation policy lives at the comms-agent layer.
Verdict: ⚠️ Patch — **outbound agent must enforce delegation policy regardless of caller** (policy evaluator runs at outbound entry, not just in comms agent). Sub-agents and skills get the same gating. Trivial extension; worth being explicit.

---

#### Conversation continuity

**ST-21 — Bob replies on day 8; Redis reply-key TTL was 7 days**
Trace: Tier-1 fails. Tier-2 (TaskID regex in subject/body) matches `TSK-123` if Bob included it. Tier-3 (vector search) restricted to Bob's tasks (`assignee.linked_user_id == USER-bob-002`) finds it. Route remains `counterparty_reply` because thread_id (Telegram chat id) is stable across the gap — even without Redis, the thread_id reveals lineage.
Verdict: ⚠️ Patch — **Gap AI (reply-key fallback via thread_id)**: keep a persistent `(channel, thread_id) → {task_id, counterparty_id}` table (Postgres, no TTL) for thread-lineage matching. Redis stays as the fast path; Postgres is the long-tail safety net.

**ST-22 — Bob replies on a different channel than original outbound**
Angela emailed Bob; Bob replies via Telegram (his preferred).
Trace: no thread continuity — different channel, different thread_id. Tier-1 fails (reply key was `email:{thread}:{msg_id}`). Tier-2 only works if Bob included TSK ID. Tier-3 may or may not resolve.
Verdict: ⚠️ Patch — **Gap AI extension**: when outbound creates a checkin, also write a content-fingerprint (subject hash, task summary embedding) into the persistent table; on cross-channel reply, fingerprint match plus sender-id resolution can recover lineage. Lower confidence than thread_id match — surfaces as "is this a reply to TSK-123?" if borderline.

**ST-23 — Bob's preferred channel changes mid-conversation**
Bob updates `preferred_channel` from telegram to whatsapp in his profile while a Telegram thread is mid-negotiation.
Trace: read-through means Angela's *next* outbound would dispatch via WhatsApp, breaking thread continuity (Bob expects to keep replying on Telegram).
Verdict: ⚠️ Patch — outbound's channel resolver should be **thread-sticky for active threads**: if there's an open `CheckinNode` thread on a channel less than N hours old, prefer that channel over the freshly-changed preference. Document on outbound channel-resolution spec.

---

#### Multi-agent per user

**ST-24 — User-1 has Angela (personal) and Felicia (work)**
PRD 15 already supports multi-agent. AgentChannelIdentity registry maps each receiving account to a `(user_id, agent_id)`.
Trace: Telegram bot @AngelaBot → (USER-1, AgentAngela). Email `felicia@…` → (USER-1, AgentFelicia). Each agent has its own profile.md, memory, policies. Inbound classifier resolves to the right agent via receiving-account lookup.
Verdict: ✅ Holds — receiving-account routing was already designed for this.
**Caveat**: **Gap AM** — admin UI to manage multiple agents per user is not in any PRD. Cockpit Settings needs a per-agent panel.

---

### 9.8.13 New gaps captured by stress tests

- **Gap AD — Counterparty detachment / freeze** (ST-01, ST-02): when `linked_user_id` becomes unreachable (member removed, account deleted), shadow freezes last-known canonical data, sets `link_status`, surfaces a one-time prompt.
- **Gap AE — Org-task-index reconciliation** (ST-08): nightly full-sync diff vs AGE truth; admin rebuild endpoint.
- **Gap AF — Distillation idempotency** (ST-06): outbox table with idempotency key per `(message_id, target)`; retry until success.
- **Gap AG — Channel-identity drift recovery** (ST-11): fuzzy fallback resolution by other signals when `AliasResolver` misses; owner-confirmed re-binding.
- **Gap AH — Owner-offline escalation queue** (ST-19): pending-decision queue + per-policy timeout fallback; surfaces in next cockpit session.
- **Gap AI — Reply-key fallback** (ST-21, ST-22): persistent `(channel, thread_id) → {task_id, counterparty_id}` table + content fingerprint for cross-channel reply lineage.
- **Gap AJ — Per-file write locks for shared MinIO state** (ST-05): extend the existing `_memory_lock` pattern to conversation `.jsonl`, profile.md, policy files; or move to versioned-write with optimistic concurrency.
- **Gap AK — Membership-change cascade** (ST-01): when `OrganizationNode.members` changes, fan-out to org-task-index ACL, user-directory, ResourceNode shadow link_status.
- **Gap AL — Mandatory org-scoped ACL at repo layer** (ST-15, ST-17): query-builder enforces `org_id IN caller.orgs AND assignee/owner predicates`; cannot be bypassed by application code.
- **Gap AM — Multi-agent admin UI** (ST-24): per-agent settings panel in cockpit; create/rename/delete additional agents per user.

Open questions — RESOLVED:
- **OQ-1 — Task ownership transfer on user deletion** (ST-16): **DECIDED — archive-tombstone**. Triggers a new system-wide principle (§9.8.17, "No-Delete Principle") that supersedes any cascade-delete behavior anywhere in the system.
- **OQ-2 — Outbound's channel-stickiness window** (ST-23): **DECIDED — configurable** in user channel preferences; default **48h**. Stored as `UserNode.preferences.channel_stickiness_hours: int = 48` (or per-channel override under the same `preferences` block).
- **OQ-3 — Fail-mode for counterparty_conversation when policies can't load** (ST-07): **DECIDED — per-policy frontmatter** `fail_mode: closed | degraded`. Default per file: delegation/escalation default `closed`; tone/etiquette default `degraded`.

### 9.8.16.5 No-Delete Principle (resolves OQ-1, applies system-wide)

This is a foundational principle — not a feature. It changes how every Gap that involves removal must be implemented.

**Principle:** No agent — main orchestrator, sub-agent, inbound, outbound, comms, or future agent type — may ever perform a hard delete of any record (nodes, edges, conversation files, intelligence entries, memory, policies, tasks, anything). Agents may only **archive + tombstone**.

**Rationale:**
- **GDPR compliance**: user-initiated "delete my data" must demonstrably remove data within a defined window — archive-then-purge satisfies this AND survives accidental triggers.
- **Catastrophic-action protection**: an LLM that can hallucinate a `delete_*` tool call is one bad turn from data loss. Removing the capability removes the failure mode entirely.
- **Auditability**: archived records remain accessible to admins for forensics, support, and dispute resolution.
- **Reversibility**: a 24h delay on user-initiated full purge gives a recovery window for catastrophic mistakes.

**Implementation contract:**

1. **Service principal model.** All graph and storage clients used by *agents* connect via a service principal (`agent_principal`) whose grants are explicitly **`SELECT, INSERT, UPDATE` only — no `DELETE`** at the database role level (Postgres `REVOKE DELETE`, AGE label-level grants, MinIO bucket policy `s3:DeleteObject` denied). This is enforced at the storage layer, not in application code.
2. **Admin/system principal** (`admin_principal`) — separate principal, has delete rights, used **only** by the scheduled purge worker and by explicit admin actions through the cockpit Admin Panel. Never injected into agent contexts.
3. **Archive primitives.** Every node/edge/file type gains an `archived_at: timestamp | null`, `archived_by: user_id | system`, `archive_reason: enum`, `purge_after: timestamp | null`. Reads default to `WHERE archived_at IS NULL`; admin/forensic queries can opt in.
4. **Tombstone redirects.** When a node is archived (e.g., merge target, deleted user's task), a `TombstoneNode` (or a tombstone field) records the redirect: `archived_id → keep_id` (for merges) or `archived_id → null` (for purges). Inbound references resolve to the redirect or detach gracefully.
5. **User-initiated full purge** (cockpit → "Delete all my data"):
   - Step 1: synchronous archive everything in `{user_id}/` substrate; mark `purge_after = now + 24h`.
   - Step 2: cockpit shows banner "Your data will be permanently deleted in 24h — undo".
   - Step 3: scheduled purge worker (admin principal) hard-deletes everything past `purge_after` that hasn't been recovered.
   - Step 4: GDPR-compliant audit trail records the purge event in an immutable log.
6. **Agent-facing tools.** Tools previously named `delete_*` are renamed `archive_*` (or removed entirely if archive isn't useful — e.g., archive-then-recover for tasks but no archive of intelligence entries; intelligence is append-only by design and trimmed via the existing 500-word limiter). The `merge_resource` tool (Gap AB) **archives** the merged-into node with a tombstone redirect rather than deleting.
7. **Counterparty detachment** (Gap AD): when a `linked_user_id` target is archived, the shadow's `link_status` flips to `detached_user_archived`; canonical data is frozen in place. If the target is later purged, link_status flips to `detached_user_purged` and any cached identity becomes read-only.
8. **Task ownership on user deletion** (ST-16 resolution): owner's tasks are archived (not deleted), `purge_after = now + 24h` if the user requested full purge. Cross-tenant assignees see them disappear from `list_external_assignments_for_me()` (filter `archived_at IS NULL`). Workspace/org admin can promote-to-org-owner before the purge window expires.

**Where this principle propagates in the design:**
- §8 Outbound agent: cannot delete `CheckinNode`s — only archive.
- §9.7 Policies: cannot delete policy files — only archive.
- §9.8.5 Gap AB: `merge_resource` archives + tombstone-redirects, never deletes.
- §9.8.13 Gap AD: detachment is archive-link-status, never delete.
- §9.8.13 Gap AE: org-task-index reconciliation removes archived rows but does not hard-delete index entries — uses `archived_at` filter on read.
- All graph repo operations: re-audit and rename any `delete_*` to `archive_*`; remove agent-callable variants.

**Testing hook:** integration tests must include a "agent attempts to delete" probe — every agent and every tool, asserting the operation is rejected at the principal layer, not just at the application layer.

**Documentation:** this is a **first-class architecture principle** — must appear at the top of `agent-subagent-design-requirements.md` and in a dedicated `data-lifecycle-and-deletion-policy.md` arch doc.

### 9.8.16.6 OQ-2 — Channel stickiness configuration

Add to existing `UserNode.preferences` (no new node):
```python
channel_stickiness_hours: int = 48
channel_stickiness_overrides: dict[str, int] = {}   # per-channel override
                                                     # e.g. {"email": 168, "telegram": 24}
```
Outbound's channel resolver (Gap B) consults this when an active `CheckinNode` thread exists less than `channel_stickiness_hours` old on a non-preferred channel — sticks with the active thread's channel rather than honoring the freshly-changed `preferred_channel`.

Editable via cockpit Settings → Channels.

### 9.8.16.7 OQ-3 — Per-policy fail mode

Add to every policy `.md` frontmatter:
```yaml
fail_mode: closed   # or 'degraded'
```
Defaults per file:
- `delegation.md` → `closed` (refuse to act on owner's behalf if can't load)
- `escalation.md` → `closed`
- `counterparty_etiquette.md` → `degraded` (use built-in safe defaults)
- `reply_tone.md` → `degraded`

Policy evaluator (§9.7) reads `fail_mode` first; on load failure, applies the configured mode rather than a global hard-fail.

### 9.8.14 Wave plan additions

- **Wave 7** absorbs **AG, AJ** (write locks + drift recovery — small, can land with onboarding).
- **Wave 8** absorbs **AK, AL** (membership cascade + ACL — pairs with directory).
- **Wave 8.5** absorbs **AD, AE** (detachment + reconciliation — pairs with cross-tenant projection).
- **Wave 9** absorbs **AM** (multi-agent admin UI — small UI add).
- **New Wave 10 — Resilience** for **AF, AH, AI** (distillation outbox, escalation queue, reply-key fallback) — these are infra-heavy and worth grouping.

### 9.8.15 Net resiliency verdict

| Category | Holds | Patch | Open |
|---|---|---|---|
| Lifecycle | ST-03, ST-04 | ST-01, ST-02 | — |
| Concurrency | — | ST-05, ST-06, ST-07, ST-08 | — |
| Identity | ST-09, ST-10, ST-12 | ST-11, ST-13 | — |
| Cross-tenant | ST-14 | ST-15, ST-17 | ST-16 |
| Policy | ST-18 | ST-19, ST-20 | — |
| Continuity | — | ST-21, ST-22, ST-23 | — |
| Multi-agent | ST-24 | (admin UI only) | — |

**Bottom line:** the design is **resilient at the conceptual layer** (no architectural rewrite triggered by any scenario), but **needs a meaningful resilience pass at the operational layer** — concurrency, retries, drift recovery, ACL enforcement, escalation queues. These are the typical "things you discover after the happy path works" gaps. They are all additive (Gaps AD–AM) and concentrated in a single new **Wave 10 — Resilience** plus small absorptions in Waves 7/8/8.5/9.

One genuine **open question** (ST-16, OQ-1): task ownership transfer policy when an owner deletes their account. Worth asking before locking the requirements doc.

### 9.8.17 No-Delete stress tests — does the principle survive contact with reality?

15 scenarios pressure-test the No-Delete principle (§9.8.16.5) across LLM-boundary attacks, infrastructure paths, principal boundaries, tombstone integrity, race/timing, GDPR compliance, and cross-tenant isolation. Same verdict legend as §9.8.12.

---

#### LLM / tool-boundary attacks

**DEL-01 — LLM hallucinates `delete_task` (no such tool)**
Trace: tool dispatcher rejects unknown tool name → LLM gets an error result → retries or apologizes. No call ever reaches the repo layer.
Verdict: ✅ Holds — existing tool-registry check.

**DEL-02 — LLM crafts a *semantic* delete via `update_task(state="PURGED")`**
Trace: today's state machine doesn't have a forbidden-states list. If the enum is permissive, an LLM could set `archived_at = now, purge_after = now - 1s` to bypass the 24h window. **Real attack surface.**
Verdict: ⚠️ Patch — **Gap AN (semantic-delete prevention)**: explicit forbidden state values for agent_principal writes; `archived_at`, `purge_after`, `link_status` and any other lifecycle field are write-restricted to admin_principal at the schema/trigger level. Agents call `archive_*` tools that compute these fields server-side under controlled logic, never accepting them as inputs.

**DEL-03 — MCP tool legitimately deletes external upstream data**
A user-authorised skill calls an MCP tool that deletes a calendar event or email message in an external system.
Trace: this is intentional and out of scope for No-Delete (which is about GraphClaw's own data). The principle protects *internal* data; agents may invoke external operations the user has authorised.
Verdict: ✅ Holds — but **scope clarification needed in arch doc**: "No-Delete applies to GraphClaw's own persistent state (graph, storage, indices). External-system mutations via MCP/skills are governed by per-tool authorisation and the user's connector consent, not by this principle."

**DEL-04 — A skill running in its own runtime accesses GraphClaw DB with broader credentials**
Skills execute in a separate worker. If the skill runtime injects admin credentials for convenience, the principle is bypassed.
Verdict: ⚠️ Patch — **Gap AP (skill runtime principal)**: skills accessing GraphClaw's own DB/storage MUST use the same `agent_principal`. Their *external-system* credentials are separate. Enforce via skill-runtime config audit + integration test.

**DEL-05 — `invoke_skill` is asked to "purge old logs"**
A user-built skill is named `cleanup_old_archive` and tries to call `delete_node`. Even if a developer wrote it, the principal blocks at the DB.
Verdict: ✅ Holds — principal layer rejects regardless of skill code intent.

---

#### Infrastructure / storage paths

**DEL-06 — Memgraph/AGE TTL or auto-vacuum deletes data outside our control**
If an admin configures TTL on a label (e.g., `INTELLIGENCE`), data vanishes without going through archive.
Verdict: ⚠️ Patch — **Gap AR (infrastructure-level deletion guards)**: explicit infrastructure-config requirements documented in `data-lifecycle-and-deletion-policy.md` — forbid TTL on user data labels; forbid MinIO bucket lifecycle expiry on user prefixes; forbid Postgres autovacuum FULL with row removal on user tables. Add a startup-time config audit check that fails the deployment if violations are detected.

**DEL-07 — MinIO bucket policy mistakenly grants `s3:DeleteObject` to agent role**
Verdict: ✅ Holds at the principle level — but caught only by **anti-delete probe tests** (already in plan). Worth running these on every deploy, not just CI.

**DEL-08 — Purge worker is down for >24h; pending-purges accumulate**
Trace: data should NOT be silently retained beyond the user's chosen purge window (GDPR concern), but also should NOT be silently lost. Need both alerting and a catch-up.
Verdict: ⚠️ Patch — **Gap AQ (purge-worker DLQ + admin alerting)**: pending-purge queue has a heartbeat; admin paged when worker hasn't run within 2× the expected interval. Worker on resume processes catch-up batch with rate limit + audit-log entries showing the late processing.

**DEL-09 — Database migration runs `DROP TABLE` or `ALTER TABLE ... DROP COLUMN`**
Migrations need DDL. They run via what principal?
Verdict: ⚠️ Patch — **third principal: `migration_principal`** with DDL grants but no DML delete grants. Used only by the migration runner, not by application code. Document in `data-lifecycle-and-deletion-policy.md`.

---

#### Principal boundary attacks

**DEL-10 — Admin principal credentials accidentally injected into agent context**
Misconfigured env var, dev-config in prod, leaked secret.
Trace: this is the highest-impact failure mode. Mitigations: (a) admin principal credentials live in a separate secrets store, never in the same env namespace agents read from; (b) startup-time assertion that the credentials the agent process loaded do NOT have delete grants (call a `BEGIN; DELETE FROM _probe; ROLLBACK;` against a probe table on init — must fail); (c) all agent connections traced with principal name in logs; alert on agent-context calls using admin principal.
Verdict: ⚠️ Patch — **strengthen Gap AL** to include: principal-name assertions at process start + structured logging of principal on every DB call.

**DEL-11 — Direct SQL tool / debug shell**
A debug/diagnostics tool that lets an operator run arbitrary SQL — what principal?
Verdict: ⚠️ Patch — **debug surfaces use admin_principal but require interactive human auth** (cockpit admin role + re-confirm); never callable by agents. Documented in `data-lifecycle-and-deletion-policy.md`.

---

#### Tombstone integrity

**DEL-12 — Multi-hop tombstone chain (A→B→C: A merged into B, then B archived in favor of C)**
Trace: `resolve_canonical(A)` should return C. Need transitive resolution with cycle detection and a sane max-hop cap.
Verdict: ⚠️ Patch — **Gap AS (tombstone chain resolver)**: single primitive `resolve_canonical(node_id)` follows redirects up to N hops (default 5), detects cycles, returns final id or raises `TombstoneChainTooDeep`/`TombstoneCycle`. All read paths use this resolver before fetching.

**DEL-13 — Conversation files under a tombstoned shadow**
After `merge_resource(keep_id=Mr.Smith, merge_id=Bob)`, paths like `conversations/{Bob_shadow_id}/telegram/{thread}.jsonl` must continue to be readable. Two options: (a) rewrite paths at archive time (concatenate Bob's files into Mr.Smith's), (b) keep both and read-through.
Verdict: ⚠️ Patch — **Gap AT (conversation-path redirect)**: at merge time, **append** Bob's `.jsonl` content into Mr.Smith's same-channel `.jsonl` (preserving timestamps via merge-sort), then write a tiny `.tombstone` redirect file at the old path so any cached references resolve. Original Bob files become archived (not deleted, per principle).

---

#### Race / timing

**DEL-14 — User clicks "Delete all my data", then logs in 12h later still using the app**
Trace: user is currently in pending-purge state. Should they be able to use the app normally? If yes, what does "use" mean — the data is archived, so reads should return empty; but they're actively typing in chat.
Verdict: ⚠️ Patch — **Gap AU (pending-purge active-user policy)**: when a user with pending purge attempts to sign in, cockpit blocks normal access and shows a single screen: **"Your data is scheduled for deletion in Xh Ym. [Cancel deletion] [Continue with deletion]"**. No app surface is loaded until the user picks. Cancelling reverts archive flags; continuing logs out and lets the timer run.

**DEL-15 — User clicks "Cancel" at 23:59:30; purge worker fires at 24:00:00**
Race window of ~30s.
Verdict: ⚠️ Patch — **purge worker re-checks `purge_after` AND `purge_cancelled_at` immediately before each delete, inside the same transaction**; cancel writes a tombstone of its own that the worker honors. The 30s cancel succeeds; race becomes a read-your-write within a single transaction.

---

#### GDPR / compliance

**DEL-16 — User invokes Right to Erasure with "delete now, no 24h delay"**
GDPR Article 17 doesn't *require* a delay; the 24h window is a UX safety net.
Verdict: ⚠️ Patch — **Gap AV (immediate-purge path)**: a separate cockpit flow ("Right to Erasure / immediate") routes through admin_principal directly, requires the user to re-authenticate, captures a justification field, writes an immutable audit entry, and runs the purge synchronously. Available to data subjects but logged distinctly from the standard 24h flow.

**DEL-17 — Legal hold prevents purge after `purge_after`**
Litigation requires data to be retained even past the user's purge window.
Verdict: ⚠️ Patch — **Gap AW (legal hold)**: archive records carry `legal_hold: bool, hold_reason, hold_set_by, hold_set_at`. Purge worker filters `WHERE purge_after < now AND legal_hold IS NOT TRUE`. Hold set/released only via admin_principal with audit entries. User is informed (per their jurisdiction's rules) that erasure is paused.

**DEL-18 — Right to Access must include archived-but-not-yet-purged data**
Trace: data export tool must read across archived AND active records (ignore `archived_at IS NULL` filter for export). Export is admin-principal-driven on the user's request.
Verdict: ✅ Holds — no patch needed beyond making the data-export tool aware of the archive flag and including archived rows by default in the user's own export. Documented in `data-lifecycle-and-deletion-policy.md`.

---

#### Cross-tenant isolation under No-Delete

**DEL-19 — User-1 archives Bob (their shadow); does anything happen to Bob's UserNode in another tenant?**
Trace: the shadow lives in User-1's substrate. Archiving it MUST NOT touch Bob's actual UserNode. Read-through of `linked_user_id` from the shadow is one-way.
Verdict: ✅ Holds — substrate isolation already prevents this; no patch needed beyond a test asserting it.

**DEL-20 — Org-archive in SaaS (the org goes out of business)**
The OrganizationNode is archived. Does that cascade to member UserNodes?
Verdict: ⚠️ Patch — **Gap AX (org-archive vs user-archive)**: archiving an `OrganizationNode` does NOT cascade to member UserNodes; members are individually offered the choice to (a) join another org, (b) become standalone (free-tier), or (c) self-archive. Their data substrate is preserved. Workspaces inside the archived org are also archived but their tasks are read-only-portable to a new org.

---

### 9.8.18 New gaps from No-Delete stress tests

- **Gap AN — Semantic-delete prevention**: lifecycle fields (`archived_at`, `purge_after`, `link_status`, etc.) write-restricted to admin_principal at schema/trigger level; agents call `archive_*` tools that compute them server-side. Forbidden state values list.
- **Gap AO — Scope clarification (external mutations)**: documented in arch doc — No-Delete is for internal state; external MCP/skill mutations are governed by per-tool consent.
- **Gap AP — Skill runtime principal**: skills accessing GraphClaw's own DB/storage use `agent_principal`; external creds are separate. Enforce via skill-runtime config audit.
- **Gap AQ — Purge-worker DLQ + admin alerting**: heartbeat + alert + catch-up batch with rate limit + audit on resume.
- **Gap AR — Infrastructure-level deletion guards**: forbid TTL on user data labels; forbid MinIO bucket lifecycle expire; startup config audit.
- **Gap AS — Tombstone chain resolver**: `resolve_canonical(node_id)` with cycle detection + max-hop cap, used by all read paths.
- **Gap AT — Conversation-path redirect on merge**: append-merge-sort `.jsonl` files; write `.tombstone` redirect at old path.
- **Gap AU — Pending-purge active-user policy**: blocking sign-in screen until user picks Cancel/Continue.
- **Gap AV — Immediate-purge path (Right to Erasure)**: separate admin-principal flow with re-auth, justification, immutable audit.
- **Gap AW — Legal hold**: `legal_hold` field + admin-only set/release + worker filter.
- **Gap AX — Org-archive does NOT cascade to UserNodes**: members offered join/standalone/self-archive.
- **Add to Gap AL strengthening**: principal-name assertions at process start + DELETE-rejection probe + structured logging of principal on every DB call.
- **Third principal: `migration_principal`**: DDL grants, no DML delete grants. Used only by migration runner.

### 9.8.19 Wave plan additions

- **Wave 0** absorbs **AN, AP, AR, AS, the migration_principal, and the principal-assertion probe**. These are foundational — they harden the No-Delete contract before any later wave's repo code is written.
- **Wave 0.5 — GDPR & lifecycle UX** for **AU, AV, AW, AX** plus the immediate-purge cockpit flow and legal-hold admin surface.
- **Wave 10 — Resilience** absorbs **AQ, AT** alongside the earlier resilience gaps (AF, AH, AI).

### 9.8.20 Net resiliency verdict for No-Delete

| Category | ✅ Holds | ⚠️ Patch | ❌ Open |
|---|---|---|---|
| LLM/tool boundary | DEL-01, DEL-03, DEL-05 | DEL-02, DEL-04 | — |
| Infrastructure | DEL-07 | DEL-06, DEL-08, DEL-09 | — |
| Principals | — | DEL-10, DEL-11 | — |
| Tombstones | — | DEL-12, DEL-13 | — |
| Race/timing | — | DEL-14, DEL-15 | — |
| GDPR | DEL-18 | DEL-16, DEL-17 | — |
| Cross-tenant | DEL-19 | DEL-20 | — |

**Bottom line**: the No-Delete *principle* survives every scenario — no architectural reversal triggered. But the *implementation contract* needs hardening at three places:
1. **Semantic-delete prevention (AN)** — without this, the principle is bypassable via `update_task(state=PURGED, archived_at=now, purge_after=now-1s)`. Highest priority.
2. **Principal-boundary enforcement (DEL-10/11 strengthening AL)** — process-start assertions and structured logging make principal violations detectable instead of silent.
3. **Infrastructure-level guards (AR)** — auto-vacuum, TTL, lifecycle policies are out-of-band deletion paths the principle doesn't intrinsically cover; needs explicit config bans.

Plus a real new requirement: **GDPR/lifecycle UX surface (Wave 0.5)** — the principle isn't complete without immediate-purge, legal-hold, and the active-user-during-pending-purge flow.

### 9.8.21 Existing-doc updates (append to §11)

- [graphclaw/docs/architecture/intelligence-layer.md](../../../Projects/graphclaw/docs/architecture/intelligence-layer.md) — add alias resolution + directory lookup to inbound/comms agent flows.
- [graphclaw/docs/agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md) — add onboarding FSM as a first-class orchestrator behaviour; document `linked_user_id` shadow pattern.
- [cockpit/docs/prd/05-settings-panel.md](docs/prd/05-settings-panel.md) — add `OrganizationNode.settings.directory_visibility` controls and identity management UI; SaaS multi-org switcher.
- [cockpit/docs/prd/09-admin-panel.md](docs/prd/09-admin-panel.md) — admin controls for org membership, directory visibility, and SSO domain — already org-scoped, extend with directory-policy UI.
- [cockpit/docs/prd/15-intelligence-hub.md](docs/prd/15-intelligence-hub.md) — show onboarding-completion state on the Profile editor; surface aliases.
- [cockpit/docs/prd/02-graph-cockpit.md](docs/prd/02-graph-cockpit.md) — workspace switcher (already designed); add a parent **org switcher** for SaaS multi-org users; surface external-assignments section in task views.
- [cockpit/docs/prd/12-task-views.md](docs/prd/12-task-views.md) — assignee-side view: distinguish locally-owned tasks vs `list_external_assignments_for_me` projections; "request access" flow for full detail.
- **NEW** [graphclaw/docs/architecture/user-identity-and-onboarding.md](../../../Projects/graphclaw/docs/architecture/user-identity-and-onboarding.md) — full spec of identity model (UserNode/ResourceNode/aliases/identities/linked_user_id), onboarding FSM, resolution algorithm, org directory schema, privacy/consent policy.
- **NEW** [graphclaw/docs/architecture/cross-tenant-task-projection.md](../../../Projects/graphclaw/docs/architecture/cross-tenant-task-projection.md) — Gap AA approach A.1: org task index schema, indexer event flow, read APIs, cross-tenant ACL, briefing-side integration, A.2 fallback for regulated tenants.
- **NEW** [graphclaw/docs/architecture/tenancy-model.md](../../../Projects/graphclaw/docs/architecture/tenancy-model.md) — formal write-up of OrganizationNode / WorkspaceNode roles, on-prem-per-org vs SaaS multi-org deployment models, membership and visibility semantics, scoping rules for resolution and indexing.
- **NEW** [graphclaw/docs/architecture/data-lifecycle-and-deletion-policy.md](../../../Projects/graphclaw/docs/architecture/data-lifecycle-and-deletion-policy.md) — **first-class architecture principle**: No-Delete by agents (§9.8.16.5). Service-principal model (`agent_principal` SELECT/INSERT/UPDATE only; `admin_principal` for purge worker only); archive primitives (`archived_at`, `purge_after`, `archive_reason`); tombstone redirects; user-initiated 24h-delayed full-purge; GDPR audit trail; testing hook (anti-delete probes). Add a top-of-document principle banner to [agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md) too.

---

## 10. Requirements document (separate deliverable)

A standalone requirements document will be created at:

**`c:\Users\abhis\Projects\graphclaw\docs\requirements\agent-triad-and-comms-substrate.md`**

It is a separate file from this plan so it can be used as the source of truth for the build plan, sprint tracking, and acceptance testing. The plan file (this document) is the *design rationale and gap analysis*; the requirements doc is the *tracked, versioned spec with IDs*.

### 10.1 Structure of the requirements doc

```
1. Overview & goals
2. Glossary (Comms agent / Inbound agent / Outbound agent / Counterparty / Owner / Thread / Receiving account)
3. Functional requirements (each with FR-ID, priority, acceptance criteria)
   3.1 Comms agent (FR-CA-*)
       - per-user profile loading, channel-agnostic chat handler,
         post-turn distillation, counterparty_conversation mode,
         delegation policy enforcement
   3.2 Inbound agent (FR-IN-*)
       - sender classification, reply-key resolution, counterparty resolution,
         routing decision matrix, intelligence + memory writes
   3.3 Outbound agent (FR-OUT-*)
       - peer-agent loop, channel resolution from preferences, drafting,
         batching window, CheckinNode + Redis reply-key creation, intelligence write
   3.4 Scheduler / Follow-up trigger (FR-SCHED-*)
       - candidate selection query, cadence config, comms-agent invocation
         contract, interrupt threshold respect
   3.5 Multi-channel chat with comms agent (FR-MC-*)
       - thread continuity, cross-channel context, reply via originating channel
   3.6 Cross-user conversations (FR-XU-*)
       - counterparty-scoped storage, AgentChannelIdentity registry,
         autonomy policy, escalation paths
   3.7 Storage / paths (FR-STORE-*)
       - conversations/{counterparty}/{channel}/{thread}.jsonl layout,
         migration from chat/history.json
   3.8 Graph schema (FR-GRAPH-*)
       - UserNode.identities, ResourceNode.identities, CheckinNode field
         additions
   3.9 Per-user policies (FR-POL-*)
       - MinIO `.md` policy files under {user_id}/agents/{agent_id}/policies/
       - YAML-frontmatter hard-limit schema + markdown body
       - Loader, Redis caching, frontmatter parser + policy evaluator
       - Intelligence Hub editor surface (PRD 15 update)
   3.10 Identity, onboarding, cross-tenant resolution (FR-ID-*)
       - Onboarding FSM (WELCOME → PERSONA → CHANNELS → WORKING_HOURS →
         PREFERENCES → POLICIES → DONE), state-as-prompt-variant + tool
         allow-list per state; resumable
       - aliases on UserNode + ResourceNode; alias-drift autoload (Gap Z)
       - linked_user_id on ResourceNode for cross-tenant shadows;
         read-through canonical preferences from linked UserNode
       - Org-level Postgres user-directory index keyed by org_id (NOT
         workspace_id); fed by UserNode + OrganizationNode.members changes
       - resolve_user(query, hints?) tool returning ranked candidates with
         confidence + source + reason; scoped to orgs the caller is a
         member of; workspace-aware preference ordering
       - create_person_via_dialog flow (FSM reused from onboarding); FIRST
         state offers top-N existing local resources before falling to "new
         external person" (Gap AB disambiguation)
       - merge_resource(keep_id, merge_id, canonical_name?) tool for
         post-hoc deduplication (Gap AB)
       - directory_visibility on OrganizationNode.settings + per-user
         discoverability on UserNode.preferences; consent semantics
   3.11 Cross-tenant task projection — A.1 (FR-XT-*)
       - Org task index (Postgres): rows per task with owner_user_id,
         org_id, workspace_id, assignee.linked_user_id[], state, deadline,
         last_activity_at, redacted summary
       - Indexer fed by event bus on task create/update/state-transition
       - list_external_assignments_for_me(filters?) and
         get_external_task_summary(task_id) tools
       - Cross-tenant ACL layer (API/repository, NOT in AGE) — state
         mutations gated by owner's delegation_policy or owner-consent flow
       - Briefing aggregation extension on the assignee side: union local
         tasks + external assignments, distinguished section
   3.12 Briefing rendering (FR-BRF-*)
       - Group by entity (assignee.node_id), not displayed name
       - Render canonical name + parenthetical aliases when >1 alias in
         window: "Bob (also: Mr. Smith) — TSK-Y, TSK-Z"
       - Duplicate-suspicion pass: fuzzy-match recently-touched
         ResourceNodes (name + aliases + identities), surface
         "possible duplicates — merge?" prompts (Gap AC)
4. Non-functional requirements
   - latency budgets per turn, batch window defaults, isolation guarantees
     (counterparty data never leaks across owners), audit logging
5. Build plan / wave breakdown (mirroring cockpit's wave format)
   - **Wave 0 (foundational, blocks all others): No-Delete principle**
             — agent_principal vs admin_principal split with REVOKE DELETE
             at Postgres/AGE/MinIO; archive primitives on every node/edge/file
             type (archived_at, archived_by, archive_reason, purge_after);
             TombstoneNode + redirects; rename agent-callable delete_* → archive_*;
             scheduled purge worker on admin_principal; anti-delete probe tests
   - Wave 1: storage + schema migrations (Gaps J, K, L, O, T, U) +
             aliases/linked_user_id fields on UserNode/ResourceNode +
             channel_stickiness_hours on UserNode.preferences (OQ-2) +
             fail_mode in policy frontmatter schema (OQ-3)
   - Wave 2: Outbound peer agent (Gap F) + preference routing (Gap B)
   - Wave 3: Inbound classification router + AgentChannelIdentity (Gaps G, M, N, P)
   - Wave 4: Comms-agent chat distillation + counterparty mode + policies
             (Gaps A, H, Q, R via §9.7 MinIO .md files)
   - Wave 5: FollowUpTrigger + scheduler (Gap I)
   - Wave 6: Channel coverage gaps — WhatsApp/Telegram pollers + per-user bots (Gap C)
   - Wave 7: Identity & onboarding (Gaps S, T, W, X, Z, AB, AC) —
             onboarding FSM, resolve_user tool, create_person_via_dialog
             with disambiguation prompt, merge_resource tool, alias-drift,
             briefing entity-grouping + duplicate-suspicion pass
   - Wave 8: Org directory + tenancy (Gaps V, Y) — Postgres directory
             index keyed by org_id, OrganizationNode.settings
             .directory_visibility + per-user discoverability,
             cross-org membership scoping for resolve_user
   - Wave 8.5: Cross-tenant task projection (Gap AA — approach A.1) —
             org task index, indexer, list_external_assignments_for_me
             + get_external_task_summary tools, ACL layer, Brian's
             briefing extension on assignee side
   - Wave 9: Cockpit UI — policies editor, conversation views,
             agent-channel identity admin, directory visibility settings,
             onboarding wizard companion in cockpit chat,
             SaaS org switcher, external-assignments view in task UI
6. Acceptance / verification matrix (FR-ID → test name)
7. Migration plan
   - chat/history.json → conversations/{user_id}/cockpit/...
   - CheckinNode field migration (backfill from intelligence log where possible)
8. Open questions / decisions log
```

Each FR follows the form:
```
### FR-OUT-005 — Channel resolution from preferences
Priority: P0
Description: ...
Inputs: ...
Outputs: ...
Acceptance:
  - Given UserNode(Bob).preferences.preferred_channel=telegram, when outbound is
    invoked with channel="auto", then dispatch occurs via Telegram adapter.
  - When channel is explicitly provided, preferences are ignored.
Dependencies: FR-GRAPH-002, FR-OUT-001
```

---

## 11. Existing documentation to update — consolidated checklist

| Doc | What to add/change |
|---|---|
| [graphclaw/docs/architecture/10-agent-loop-orchestration.md](../../../Projects/graphclaw/docs/architecture/10-agent-loop-orchestration.md) | Post-turn distillation step; channel-agnostic chat handler signature `(user_id, text, channel, thread_id, session_id)`; `counterparty_conversation` mode |
| [graphclaw/docs/architecture/intelligence-layer.md](../../../Projects/graphclaw/docs/architecture/intelligence-layer.md) | Distillation applies to all sources incl. web chat; sender classification matrix; outbound preference resolution; counterparty memory tagging |
| [graphclaw/docs/agent-subagent-design-requirements.md](../../../Projects/graphclaw/docs/agent-subagent-design-requirements.md) | Outbound is a peer agent; agent-channel-identity registry; delegation policy on UserNode.preferences |
| **NEW** [graphclaw/docs/architecture/agent-triad.md](../../../Projects/graphclaw/docs/architecture/agent-triad.md) | The comms/inbound/outbound triad, shared substrate, message flow diagrams (§8.5), routing decision matrix (§9.3) |
| **NEW** [graphclaw/docs/architecture/follow-up-cadence.md](../../../Projects/graphclaw/docs/architecture/follow-up-cadence.md) | FollowUpTrigger design, candidate selection query, interrupt-threshold semantics |
| **NEW** [graphclaw/docs/architecture/cross-user-conversations.md](../../../Projects/graphclaw/docs/architecture/cross-user-conversations.md) | Counterparty-scoped storage layout (§9.2), reply-key linking, autonomy policy |
| **NEW** [graphclaw/docs/requirements/agent-triad-and-comms-substrate.md](../../../Projects/graphclaw/docs/requirements/agent-triad-and-comms-substrate.md) | The tracked requirements doc per §10 |
| [cockpit/docs/prd/13-chat-interface.md](docs/prd/13-chat-interface.md) | Web chat is one channel of many; comms agent maintains unified context across channels; replies flow back on originating channel; counterparty conversations are visible but separate from owner-self chat |
| [cockpit/docs/prd/15-intelligence-hub.md](docs/prd/15-intelligence-hub.md) | Working memory accumulates from all three agents and all channels; show counterparty-tagged notes; **add Policies section** (delegation / counterparty etiquette / escalation / reply tone) with form-on-frontmatter + markdown-body editor |
| [cockpit/docs/prd/03-agent-monitor.md](docs/prd/03-agent-monitor.md) | Surface distillation events; show outbound agent activity; show scheduler-driven runs |
| [cockpit/docs/prd/05-settings-panel.md](docs/prd/05-settings-panel.md) | Add `AgentChannelIdentity` admin (per-user channel accounts); add `delegation_policy` controls |
| [cockpit/docs/prd/12-task-views.md](docs/prd/12-task-views.md) | Task detail must surface counterparty conversations linked via CheckinNode, separate from owner discussion |
| [cockpit/docs/prd/11-api-contract.md](docs/prd/11-api-contract.md) | New endpoints: `GET /conversations/{counterparty_id}`, `POST /agent-channels`, scheduler/trigger admin endpoints |
| [graphclaw/docs/prd/](../../../Projects/graphclaw/docs/) (or equivalent) | Schema additions: UserNode.identities, ResourceNode.identities, UserNode.preferences.delegation_policy, CheckinNode.{recipient_id, channel, thread_id, direction} |
