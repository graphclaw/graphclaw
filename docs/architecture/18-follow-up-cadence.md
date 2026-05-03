# 18 — Follow-Up Cadence: Scheduler-Driven Comms

**Status:** Draft v1.0 | **Date:** 2026-05-02

This document specifies the scheduler-driven follow-up flow, where the comms agent acts on tasks even when the user is not actively chatting. Builds on the existing trigger scaffold (`triggers/briefing.py`, `agent/briefing.py`) and the comms/inbound/outbound triad.

Companion docs: [14-agent-triad.md](14-agent-triad.md), [intelligence-layer.md](intelligence-layer.md). Source plan §8.3.

---

## 1. Principle

The user being offline is irrelevant — the triad runs headlessly on cron ticks and surfaces only via outbound channels (and a feed entry in cockpit when the user next opens it).

```
cron tick → FollowUpTrigger → candidate selection → comms agent (trigger mode)
       → reasoning → outbound dispatch → CheckinNode + intelligence + memory
       → reply (eventually) → inbound classifier → counterparty_reply route
```

---

## 2. FollowUpTrigger (FR-SCHED-001)

### 2.1 Cadence
- Runs every 15 min by default (admin-tunable).
- Per-user effective cadence derived from `UserNode.preferences.briefing_time` and `default_follow_up_days`.

### 2.2 Candidate selection
For each user with at least one active task:
```sql
SELECT task_id
FROM tasks
WHERE owner_user_id = :user_id
  AND state IN ('WAITING', 'IN_PROGRESS')
  AND archived_at IS NULL
  AND (now() - last_outbound_at) >= (:follow_up_days * INTERVAL '1 day')
  AND interrupt_threshold_ok(:user_id, last_briefing_at)
```

`last_outbound_at` is derived from the most recent outbound CheckinNode (FR-GRAPH-004 fields).

### 2.3 Invocation
```python
MainOrchestrator.process_trigger(
    user_id=user_id,
    trigger="follow_up_review",
    payload={"candidate_task_ids": [...]},
    session_id=...,
)
```

The comms agent treats `trigger=follow_up_review` as a synthetic system message: "Review these N candidates and decide follow-ups." Its **normal loop runs unchanged** — same reasoning, same tools, same post-turn distillation (FR-CA-002).

### 2.4 Outputs
- Outbound dispatches (FR-OUT-*).
- `node.intelligence` updates per task touched.
- `working/context.md` memory_note ("ran follow-up review at … reached out to N counterparties").
- Optional cockpit feed entry visible on next session.

---

## 3. Owner-offline escalation (FR-SCHED-002)

When the comms agent (or outbound) needs the owner's approval but the owner hasn't responded:
1. Item enqueued in `escalation_queue` Postgres table:
   ```sql
   CREATE TABLE escalation_queue (
     id              UUID PRIMARY KEY,
     user_id         TEXT NOT NULL,
     context_ref     TEXT,            -- node_id or thread_id
     prompt          TEXT,            -- what the agent needs decided
     proposed_action JSONB,
     created_at      TIMESTAMPTZ,
     expires_at      TIMESTAMPTZ,     -- per-policy timeout
     resolved_at     TIMESTAMPTZ,
     resolution      TEXT             -- "approved"|"rejected"|"timeout_fallback"
   );
   ```
2. Cockpit shows pending-decisions banner on next session.
3. If `expires_at` reached without resolution, the policy's `on_owner_unreachable_after_hours` setting determines fallback (e.g., `hold` until next session, or `escalate_to_workspace_admin`).

---

## 4. Integration with policies

The comms agent in trigger mode loads the same per-user policies (FR-POL-001) as in normal chat mode:
- **Delegation policy** — what the agent may do unsupervised.
- **Escalation policy** — when to interrupt the owner.
- **Counterparty etiquette** — tone for outbound drafts.
- **Reply tone** — voice.

Hard limits in YAML frontmatter are enforced before any LLM call; soft guidance in the markdown body is injected into the system prompt.

---

## 5. Existing precedent

The `BriefingTrigger` already runs on a similar schedule:
- [src/graphclaw/triggers/briefing.py](../../src/graphclaw/triggers/briefing.py)
- [src/graphclaw/agent/briefing.py](../../src/graphclaw/agent/briefing.py)
- [src/graphclaw/agent/event_consumer.py](../../src/graphclaw/agent/event_consumer.py) (writes intelligence on trigger events)

`FollowUpTrigger` follows the same pattern. The two triggers may share infrastructure (cron framework, principal management, structured logging).

---

## 6. Files

### Existing
| Concern | File |
|---|---|
| Briefing trigger (precedent) | [src/graphclaw/triggers/briefing.py](../../src/graphclaw/triggers/briefing.py) |
| Briefing agent loop | [src/graphclaw/agent/briefing.py](../../src/graphclaw/agent/briefing.py) |
| Event consumer | [src/graphclaw/agent/event_consumer.py](../../src/graphclaw/agent/event_consumer.py) |

### To create
| FR | File | Purpose |
|---|---|---|
| FR-SCHED-001 | new `src/graphclaw/triggers/follow_up.py` | Cron candidate selection + invocation |
| FR-SCHED-001 | [src/graphclaw/agent/main_orchestrator.py](../../src/graphclaw/agent/main_orchestrator.py) | `process_trigger(user_id, trigger, payload, session_id)` entry point |
| FR-SCHED-001 | new `src/graphclaw/api/admin/triggers.py` | Admin tuning endpoint |
| FR-SCHED-002 | new `src/graphclaw/agent/escalation.py` | Queue management |
| FR-SCHED-002 | new migration `0XX_escalation_queue.py` | Table |
| FR-SCHED-002 | `cockpit/src/features/cockpit/PendingDecisionsBanner.tsx` | Cockpit surface |
