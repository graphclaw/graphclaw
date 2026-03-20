# Task Graph Management System — Review Notes

**Document:** task-graph-requirements.md (v1.1 — Observability, Operations & Deployment)
**Originally Reviewed:** 2026-03-17 (v0.9)
**Updated:** 2026-03-17 (v1.1 review — Sections 31-32 added, 58 design principles)
**Status:** Parked for discussion

---

## Overall Assessment

The PRD is architecturally sound and exceptionally detailed. The property graph model, agent reasoning loop, explainability-first design, and 44 design principles are coherent and well-developed. The areas below are not blockers — they are gaps and edge cases to resolve before moving to implementation.

---

## Issues to Discuss

### 1. Context Window Pressure at Scale

**Where:** Section 9 (Agent Reasoning Algorithm) vs. Principle 26 (Progressive loading over full context)  
**Issue:** Principle 26 commits to progressive loading but the reasoning algorithm doesn't specify *how* this is enforced in practice. At scale — a user with 500+ tasks, deeply nested goals, and multiple active skill agents — the agent's reasoning prompt could grow very large.  
**Suggested fix:** Define explicitly what gets loaded per trigger type. For example:
- Time-based trigger → load top-N scored tasks only
- Event-based trigger → load the changed node + its immediate neighbors
- Inbound update → load matched task + parent goal + resource node
- On-demand → load full context up to a defined token budget

---

### 2. Scoring Weight Learning Mechanism

**Where:** Section 4.1 (UserNode `scoring_weights`), Section 9  
**Issue:** The `scoring_weights` block on `UserNode` references weights that are "learned over time" but the learning mechanism is completely unspecified. "Learns from every interaction" is currently aspirational.  
**Questions to answer:**
- How many override signals before a weight adjusts?
- What is the adjustment magnitude per signal?
- Is there a floor/ceiling per weight?
- Does a manual snooze signal differently than a re-prioritization?
- Is the learning model simple (gradient nudge) or more complex?

---

### 3. Multi-User Graph Conflict Resolution

**Where:** Section 19 (Multi-User Graph)  
**Issue:** Two agents acting for different users on a shared node could take contradictory actions simultaneously. The "agents coordinate through the graph" principle (Principle 15) establishes intent but doesn't define the mechanism.  
**Suggested fix:** Specify a node-level optimistic locking pattern (e.g., `version` field on nodes, write rejected if version mismatch) and define what happens on conflict — last write wins, alert both users, or queue for human resolution.

---

### 4. Recurring Task Missed Spawn Handling

**Where:** Section 4.3.1 (RECURRING `type_metadata`)  
**Issue:** If the container is down when a spawn is due, the `next_spawn_at` will pass without a new instance being created. There is no spec for catch-up behavior.  
**Options to decide between:**
- **Catch up:** Spawn the missed instance immediately on restart
- **Skip:** Log the miss, advance `next_spawn_at` to the next scheduled time
- **Alert:** Notify the human and let them decide
- **Configurable per task:** Add a `missed_spawn_behavior` field to `type_metadata`

---

### 5. Approval Task Deadlock / Escalation Path

**Where:** Section 3.1 (Approval Task), Section 4.3.1 (APPROVAL `type_metadata`)  
**Issue:** Approval Tasks explicitly cannot be auto-resolved. If the approver goes silent, the entire downstream chain is blocked indefinitely. No timeout or escalation path is defined.  
**Suggested fix:** Add to APPROVAL `type_metadata`:
- `max_wait_days: integer` — after which the agent escalates
- `escalation_target: user_id` — who gets notified if approver is unresponsive
- `escalation_action: "ALERT" | "REASSIGN" | "CANCEL_DOWNSTREAM"`

---

### 6. Skill Agent Failure Recovery

**Where:** Section 30 (Skill Agent Runtime)  
**Issue:** The heartbeat → 15-min timeout → failure signal path is defined, but what the orchestrating agent does *after* detecting failure is not. The failure detection mechanism is complete; the recovery decision tree is missing.  
**Questions to answer:**
- Does the agent retry automatically? How many times?
- Does it reassign to a different skill agent?
- Does it always alert the human, or only after retries are exhausted?
- What happens to the partial output folder (S3) from the failed run — is it preserved, cleared, or archived?
- Is there a distinction between a hung agent (timeout) and a crashed agent (no heartbeat at all)?

---

### 7. Vector Embedding Staleness

**Where:** Section 4.3 (TaskNode `embedding`), Section 4.2 (ResourceNode `embedding`)  
**Issue:** Embeddings are used for inbound update matching. If a task's title, description, or goal context changes significantly after the embedding was computed, matches may be missed or incorrect. There is no recompute trigger or staleness policy defined.  
**Suggested fix:**
- Add `embedding_computed_at: timestamp` to both TaskNode and ResourceNode
- Define a recompute trigger: any write to `embedding_inputs` fields should enqueue a re-embed job
- Consider a staleness threshold (e.g., flag if `embedding_computed_at` > 7 days old and `updated_at` is more recent)

---

## Minor Gaps

| # | Gap | Location | Suggested Fix |
|---|-----|----------|---------------|
| M1 | `INACTIVE_PENDING` state is in the TaskNode schema but not in the state machine section | Section 4.3, Section 7 | Add `INACTIVE_PENDING` to the state machine with entry/exit conditions |
| M2 | No ordering spec for tasks within a composed Check-in message | Section 4.6 (CheckinNode) | Specify that tasks are ordered by `computed_priority` descending within the outbound message |
| M3 | `briefing_style: "concise" | "detailed"` has no structural definition | Section 4.1 (UserNode preferences), Section 12 | Define what differs structurally — e.g., concise = top 5 tasks + one-liner each; detailed = full score breakdown + topology note per task |

---

## Not Issues — Deliberate Calls Worth Acknowledging

These are design decisions that could raise questions but appear intentional and sound:

- **No direct agent-to-agent communication** (Principle 15) — coordination through the graph is the right call for auditability and avoiding race conditions.
- **Approval Tasks cannot be auto-resolved** — correct, this is a trust boundary that should not be crossed.
- **Archive, never delete** (Principle 21) — good for auditability; just ensure the scoring loop explicitly excludes archived nodes to avoid performance drag at scale.
- **Container per user** (Principle 29) — strong isolation model; the ops overhead is worth it.

---

---

## Extended Review — Architecture, Design, Requirements, Concepts

*Added: 2026-03-17 — Comprehensive review across all dimensions*

### Architecture Issues

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| A1 | **File-based locking race condition** — `pending_actions.md` read-check-write is not atomic. Two triggers arriving within ms can both read "no lock", both proceed, corrupt state. | HIGH | Sec 26 (Orchestrating Agent) |
| A2 | **Container-per-user cost at scale** — At 10K+ users, always-on containers are unsustainable. Need idle-to-zero scaling (e.g., Knative, AWS Fargate spot) or a warm-pool model where containers spin up on trigger and scale down after idle timeout. | HIGH | Sec 28, Principle 29 |
| A3 | **Polyglot persistence operational burden** — Running Neo4j/AGE + Postgres + Redis + S3 + pgvector is 5 distinct systems to operate, backup, monitor, and version. Consider starting with Postgres + AGE + pgvector (single engine) and only adding Redis/Neo4j when benchmarks demand it. | MEDIUM | Sec 27, Principle 27 |
| A4 | **SQS naming contradicts cloud-agnostic principle** — The doc names SQS queues, SQS visibility timeouts, and SQS DLQ policies throughout. Abstract behind a `MessageBroker` interface from day one. Implementations: SQS, Google Pub/Sub, Azure Service Bus, BullMQ (local dev). | MEDIUM | Sec 29, Principle 31 |
| A5 | **Channel gateway is a single point of failure** — One gateway container handles all inbound webhooks. If it goes down, all channels are dark. Need at least 2 replicas behind a load balancer, with health-check-based failover. | MEDIUM | Sec 29.2 |
| A6 | **No rate limiting or abuse prevention** — No mention of rate limiting on the A2A API, channel webhooks, or user-facing endpoints. A rogue external agent could flood the inbound queue. | MEDIUM | Sec 30.9 |

### Design Issues

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| D1 | **Scoring model blind spot: decision fatigue** — The 7-factor model scores task urgency but not cognitive load. Surfacing 15 high-urgency tasks simultaneously is counterproductive. Need a `max_surface_count` or fatigue-aware batching layer on top of the scoring output. | MEDIUM | Sec 9 |
| D2 | **Scoring model blind spot: cross-goal conflict** — Two goals may compete for the same time slot or resource. The scoring model evaluates tasks within their goal context but has no cross-goal arbitration mechanism. | MEDIUM | Sec 9, Sec 11 |
| D3 | **No explicit error taxonomy** — Failures are described per-component but there's no unified error classification (transient vs. permanent, user-facing vs. system, retryable vs. terminal). This will lead to inconsistent error handling across components. | LOW | System-wide |
| D4 | **MD file format lacks schema versioning** — As the system evolves, `soul.md`, `profile.md`, `assets.md` formats will change. No migration strategy for existing file schemas. Add a `schema_version` frontmatter field to all MD files. | LOW | Sec 26 |

### Requirements Gaps

| # | Gap | Impact |
|---|-----|--------|
| R1 | **Missing Slack/Teams channel support** — Enterprise adoption is severely limited without Slack and Microsoft Teams. These should be Phase 2 or Phase 3 additions, not afterthoughts. | HIGH |
| R2 | **No offline/degraded mode** — If LLM API is down, the entire system is inert. Need a graceful degradation path: queue triggers, surface raw task list, allow manual state updates. | MEDIUM |
| R3 | **No bulk import/migration** — Users with existing task systems (Jira, Asana, Notion, Trello) have no onramp. Need an import protocol that maps external task structures to the graph model. | MEDIUM |
| R4 | **No calendar/scheduling integration** — Tasks have deadlines but no calendar awareness. Meetings, holidays, and blocked time are invisible to the scoring model. Google Calendar / Outlook integration needed. | MEDIUM |

### Concept / Principle Challenges

| # | Challenge | Discussion |
|---|-----------|------------|
| C1 | **"Archive never delete" + GDPR right-to-erasure conflict** — GDPR Article 17 requires data deletion on user request. "Archive never delete" (Principle 21) must have a legal exception path: anonymize PII fields while preserving graph structure for audit integrity. | HIGH |
| C2 | **"Agent acts, human decides" under time pressure** — If a deadline is in 2 hours and the human is unreachable, the agent is stuck. Need a configurable escalation-to-autonomy model: "if human unreachable for X hours before deadline, agent may take action Y." | MEDIUM |
| C3 | **Learning model without ML infrastructure** — "Learns from every interaction" currently means nudging scoring weights. At scale, this needs a proper feedback loop (signal collection -> batch weight update -> validation). A simple exponential moving average per weight, updated on each override signal, is sufficient for Phase 0-2. Defer ML pipeline to Phase 4+. | LOW |
| C4 | **PII handling unspecified** — User names, emails, phone numbers, meeting notes, and business data flow through the system. No data classification, encryption-at-rest policy, or PII masking strategy is defined. | HIGH |

---

## Issue Summary

| Category | HIGH | MEDIUM | LOW | Total |
|----------|------|--------|-----|-------|
| Original Issues (#1-7) | 2 | 3 | 2 | 7 |
| Architecture (A1-A6) | 2 | 4 | 0 | 6 |
| Design (D1-D4) | 0 | 2 | 2 | 4 |
| Requirements (R1-R4) | 1 | 3 | 0 | 4 |
| Concepts (C1-C4) | 2 | 1 | 1 | 4 |
| Minor Gaps (M1-M3) | — | — | — | 3 |
| **Total** | **7** | **13** | **5** | **28** |

---

---

## v1.1 Resolution Status

The following review issues are **addressed or resolved** by v1.1 additions (Sections 31-32):

| Issue | Status | How v1.1 Addresses It |
|-------|--------|----------------------|
| A4 (SQS naming contradicts cloud-agnostic) | **Partially resolved** | Sec 31.6 adds SecretsClient abstraction with pluggable backends. SQS naming in queue architecture still AWS-specific — MessageBroker abstraction still needed. |
| A6 (No rate limiting) | **Resolved** | Sec 31.7 specifies rate limits: 1,000 req/min per IP on webhooks, 100 req/min per A2A key. |
| D3 (No error taxonomy) | **Partially resolved** | Sec 32.7 defines 3-tier alerting (P1/P2/P3) with clear error classification. Per-component error taxonomy still unspecified but the alerting model provides a framework. |
| D4 (MD file lacks schema versioning) | **Resolved** | Sec 32.10 specifies schema_version in main.md, forward-only idempotent migrations, version-stamped files. |
| C4 (PII handling unspecified) | **Partially resolved** | Sec 31.7 Surface 9 addresses prompt injection. Sec 32.3 specifies log scrubbing for secrets. IP addresses hashed in audit logs. However, full PII classification/masking for task content still undefined. |
| R2 (No offline/degraded mode) | **Partially resolved** | Sec 32.4 specifies async logging that survives CloudWatch outages. Sec 32.9 rolling deployment absorbs restarts. However, LLM API unavailability graceful degradation still unspecified. |

### Issues Still Open After v1.1

| Priority | Count | Issues |
|----------|-------|--------|
| HIGH | 5 | A1 (file locking), A2 (container cost), C1 (GDPR), C4 (PII — partial), R1 (Slack/Teams) |
| MEDIUM | 9 | A3, A4 (partial), A5, D1, D2, R2 (partial), R3, R4, C2 |
| LOW | 2 | C3, D3 (partial) |

### New Observations from v1.1

| # | Observation | Type |
|---|-------------|------|
| V1 | **Section 31 is AWS-heavy despite cloud-agnostic principle** — IAM policies, SQS ARNs, ECS task roles are AWS-native throughout. The SecretsClient (31.6) and IAM mapping (31.9) provide portability escape hatches, but the primary spec reads as "AWS with alternatives noted." This is pragmatic but should be explicit: "AWS-first, cloud-portable." | DESIGN NOTE |
| V2 | **Observability is CloudWatch-native** — Sec 32 uses CloudWatch Logs, Logs Insights, metric filters, and alarms throughout. No mention of OpenTelemetry. For non-AWS deployments, the entire observability stack needs re-implementation. Consider OpenTelemetry as the instrumentation layer with CloudWatch as one backend. | MEDIUM |
| V3 | **AsyncLogger drops logs silently on buffer overflow** — Sec 32.4 shows `queue.Full: pass`. At 10K buffer, a burst of 10K+ events loses data silently. Should at minimum increment a counter metric for dropped logs. | LOW |
| V4 | **58 design principles may be too many to enforce** — v0.9 had 44, v1.1 has 58. These are valuable for documentation but impractical as a checklist for every code review. Consider grouping into 5-6 principle categories with a "top 10 non-negotiable" subset. | DESIGN NOTE |
| V5 | **Security section assumes single-region deployment** — VPC endpoints, RDS Multi-AZ, and CloudTrail are region-scoped. Multi-region / data residency requirements (mentioned in build plan Phase 5) need additional architecture for cross-region replication, regional isolation, and data sovereignty. | MEDIUM |

---

*End of review notes. All items above are parked for discussion — none are blocking the current draft stage.*
