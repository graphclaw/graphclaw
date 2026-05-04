# GraphClaw — Architecture Documentation

> **Purpose:** A hands-on guide to understand the mechanics, module structure, and extensibility points of GraphClaw.  
> Every diagram in this folder is rendered from Mermaid source embedded in Markdown — no external tooling required.

---

## Document Index

| Document | What it covers |
|----------|---------------|
| [01 — Solution Overview](01-solution-overview.md) | What GraphClaw is, the six capability layers, high-level block diagram |
| [02 — Project Structure](02-project-structure.md) | Folder layout, module responsibilities, extensibility map |
| [03 — Class Diagrams](03-class-diagrams.md) | Core abstractions, inheritance trees, service relationships |
| [04 — Graph DB Schema](04-graph-schema.md) | Node types, edge types, property model, entity-relationship diagram |
| [05 — Data Flow & UML](05-data-flow.md) | Inbound message lifecycle, outbound delivery, agent loop sequence diagrams |
| [06 — Local Deployment](06-deployment-local.md) | Docker Compose stack, container wiring, port map |
| [07 — AWS Deployment](07-deployment-aws.md) | ECS Fargate topology, managed services, IAM, ALB routing |
| [08 — Object Storage Model](08-object-storage-model.md) | Bucket layout, multi-tenant isolation, StoragePaths API, object lifecycle |
| [09 — Infrastructure Abstractions](09-infrastructure-abstractions.md) | ABC pattern, backend swapping (MinIO→S3, Redis→SQS, env→Vault), **two-tier caching design** (in-process TTL + Redis), full env var reference, cockpit full-stack compose |
| [10 — Agent Loop Orchestration](10-agent-loop-orchestration.md) | Main orchestrator turn loop, system-prompt assembly, tool execution |
| [11 — Sub-Agent Orchestration](11-sub-agent-orchestration.md) | Delegation, dispatch planning, sub-agent lifecycle |
| [12 — Intelligence Hub Architecture](12-intelligence-hub-architecture.md) | Profile, memory tiers (working/episodic/semantic), compact, skills authoring |
| [13 — Tenancy Model](13-tenancy-model.md) | OrganizationNode + WorkspaceNode roles, on-prem vs SaaS multi-org |
| [14 — Agent Triad (Comms / Inbound / Outbound)](14-agent-triad.md) | Three-agent peer architecture, distillation contract, routing matrix |
| [15 — User Identity, Onboarding, Resolution](15-user-identity-and-onboarding.md) | Identities, aliases, linked_user_id, onboarding FSM, name resolution + merge |
| [16 — Cross-User (Counterparty) Conversations](16-cross-user-conversations.md) | Counterparty-scoped storage, reply-key linking, multi-channel chat |
| [17 — Cross-Tenant Task Projection (A.1)](17-cross-tenant-task-projection.md) | Org task index, list_external_assignments_for_me, ACL |
| [18 — Follow-Up Cadence](18-follow-up-cadence.md) | Scheduler-driven follow-ups, escalation queue |
| [19 — Data Lifecycle & Deletion Policy (No-Delete)](19-data-lifecycle-and-deletion-policy.md) | **Foundational:** service-principal split, archive+tombstone, GDPR flows |
| [20 — Agent Activity Logging](20-agent-activity-logging.md) | Logging pipeline (5 agent files → MinIO → activity feed), `agent_session_log` table, plain-language formatter, MinIO write race fix. Backs the cockpit's Agent Monitor v2. |

> **Requirements bundle** (in `docs/requirements/`):
> - [agent-triad-and-comms-substrate.md](../requirements/agent-triad-and-comms-substrate.md) — actionable spec (FR-IDs, files, acceptance, wave plan)
> - [build-readiness.md](../requirements/build-readiness.md) — **read first before Wave 0**: kickoff PR sequence, rollout/migration ordering, verification matrix, API shapes, risk register, open-questions log
> - [review-the-design-plans-squishy-eagle.md](../requirements/review-the-design-plans-squishy-eagle.md) — design-conversation trail (gaps A–AX, validation walkthroughs, stress tests, decision rationale)
> - [agent-monitor-v2-backend.md](../requirements/agent-monitor-v2-backend.md) — backend functional requirements for Agent Monitor v2 (B-1..B-9: tool call wiring, session log, /agent/activity, /comms/summary, /tasks/{in,out}bound-log, MinIO race fix)

---

## Quick-Reference: Key URLs (Local Dev)

| Service | URL |
|---------|-----|
| API Gateway / Swagger UI | http://localhost:8000/docs |
| API Gateway / ReDoc | http://localhost:8000/redoc |
| pgAdmin 4 | http://localhost:5050 |
| MinIO Console | http://localhost:9001 |
| Health check | http://localhost:8000/health |

---

## Technology Stack at a Glance

```
Language        Python 3.12
Web Framework   FastAPI + Uvicorn
Graph DB        PostgreSQL 18 + Apache AGE + pgvector
Cache / Broker  Redis 7
Object Storage  MinIO (local) / AWS S3 (cloud)
Auth            OAuth 2.0 (Google / GitHub / Microsoft) + RS256 JWT
LLMs            Anthropic Claude, OpenAI, LiteLLM (multi-provider)
MCP             Model Context Protocol — tool calls to external services
Channels        Email, Slack, Teams, Telegram, WhatsApp
Connectors      Jira, Notion, Asana, Google Calendar, Outlook Calendar
Containers      Docker Compose (local) / ECS Fargate (AWS)
```
