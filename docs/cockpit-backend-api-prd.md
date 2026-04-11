# Cockpit Backend API — Implementation Backlog

**Version:** 1.0 | **Date:** 2026-03-21 | **Status:** Draft
**Companion:** [graphclaw-cockpit PRD](https://github.com/abhishekgupta-myrepo/graphclaw-cockpit) — `docs/prd/11-api-contract.md`

---

## Purpose

This document lists every backend API endpoint the GraphClaw Cockpit UI requires. It is the **implementation backlog** for the backend team. Each endpoint is categorized by priority and mapped to the cockpit PRD section that defines its contract.

---

## Existing Endpoints (Already Implemented)

These endpoints exist in the codebase. Some are stubs (in-memory) and need real implementations.

| # | Path | Status | Location | Notes |
|---|------|--------|----------|-------|
| 1 | `GET /auth/login` | Real | `auth/routes.py` | OAuth 2.0 + PKCE |
| 2 | `GET /auth/callback` | Real | `auth/routes.py` | |
| 3 | `POST /auth/refresh` | Real | `auth/routes.py` | |
| 4 | `POST /auth/logout` | Real | `auth/routes.py` | |
| 5 | `GET /auth/me` | Real | `auth/routes.py` | |
| 6 | `POST /api/v1/inbound` | Real | `gateway/app.py` | |
| 7 | `POST /api/v1/outbound` | Real | `gateway/app.py` | |
| 8 | `POST /api/v1/trigger` | Real | `gateway/app.py` | |
| 9 | `GET/POST /webhooks/whatsapp` | Real | `gateway/app.py` | |
| 10 | `GET/POST /webhooks/telegram` | Real | `gateway/app.py` | |
| 11 | `GET /health` | Real | `api/health.py` | |
| 12 | `GET /health/ready` | Real | `api/health.py` | |
| 13 | `GET /ready` | Real | `api/health.py` | |
| 14 | `GET /app/v1/settings` | **Stub** | `api/routes.py` | Needs: S3 config read |
| 15 | `PATCH /app/v1/settings` | **Stub** | `api/routes.py` | Needs: S3 config write |
| 16 | `GET /app/v1/settings/channels` | **Stub** | `api/routes.py` | Needs: real channel status |
| 17 | `GET /app/v1/approvals` | **Stub** | `api/routes.py` | Needs: graph query |
| 18 | `POST /app/v1/approvals/{id}/approve` | **Stub** | `api/routes.py` | Needs: state transition |
| 19 | `POST /app/v1/approvals/{id}/deny` | **Stub** | `api/routes.py` | Needs: state transition |
| 20 | `GET /app/v1/skills` | **Stub** | `api/routes.py` | Needs: registry query |
| 21 | `POST /app/v1/skills/install` | **Stub** | `api/routes.py` | |
| 22 | `DELETE /app/v1/skills/{id}` | **Stub** | `api/routes.py` | |
| 23 | `GET /app/v1/skills/search` | **Stub** | `api/routes.py` | |
| 24 | `GET /app/v1/skills/sources` | **Stub** | `api/routes.py` | |
| 25 | `POST /app/v1/skills/sources` | **Stub** | `api/routes.py` | |
| 26 | `DELETE /app/v1/skills/sources/{uri}` | **Stub** | `api/routes.py` | |
| 27 | `GET /app/v1/mcp-servers` | **Stub** | `api/routes.py` | |
| 28 | `POST /app/v1/mcp-servers` | **Stub** | `api/routes.py` | |
| 29 | `PATCH /app/v1/mcp-servers/{id}` | **Stub** | `api/routes.py` | |
| 30 | `DELETE /app/v1/mcp-servers/{id}` | **Stub** | `api/routes.py` | |
| 31 | `GET /app/v1/mcp-servers/search` | **Stub** | `api/routes.py` | |
| 32 | `GET /app/v1/compliance/export` | Real | `compliance/` | |
| 33 | `POST /app/v1/compliance/erasure` | Real | `compliance/` | |
| 34 | `GET /app/v1/compliance/erasure/{id}` | Real | `compliance/` | |
| 35 | `POST /api/v1/a2a/agents` | Real | `a2a/routes.py` | |
| 36 | `GET /api/v1/a2a/agents` | Real | `a2a/routes.py` | |
| 37 | `DELETE /api/v1/a2a/agents` | Real | `a2a/routes.py` | |
| 38 | `POST /api/v1/task-update` | Real | `a2a/routes.py` | |

---

## New Endpoints Required

### Priority 1 — Core Cockpit (Graph + Tasks + Scoring)

Required for the basic cockpit surface to function (PRD §02, §08, §12).

| # | Method | Path | PRD Ref | Backend Module | Dependencies |
|---|--------|------|---------|----------------|-------------|
| 1 | GET | `/app/v1/graph/goals` | §02, §11.2 | `api/graph.py` (new) | GraphStore |
| 2 | GET | `/app/v1/graph/goals/{id}/tree` | §02, §11.2 | `api/graph.py` | GraphStore, GraphQueryEngine |
| 3 | GET | `/app/v1/graph/tasks` | §02, §11.2, §12 | `api/graph.py` | GraphStore |
| 4 | GET | `/app/v1/graph/tasks/{id}` | §02, §11.2 | `api/graph.py` | GraphStore, ScoringEngine |
| 5 | POST | `/app/v1/graph/tasks` | §02, §11.2 | `api/graph.py` | GraphStore |
| 6 | PATCH | `/app/v1/graph/tasks/{id}` | §02, §11.2 | `api/graph.py` | GraphStore, StateMachine |
| 7 | DELETE | `/app/v1/graph/tasks/{id}` | §02, §11.2 | `api/graph.py` | GraphStore |
| 8 | GET | `/app/v1/graph/resources` | §02, §11.2 | `api/graph.py` | GraphStore |
| 9 | GET | `/app/v1/graph/edges` | §02, §11.2 | `api/graph.py` | GraphStore |
| 10 | POST | `/app/v1/graph/edges` | §02, §11.2 | `api/graph.py` | GraphStore |
| 11 | DELETE | `/app/v1/graph/edges/{id}` | §02, §11.2 | `api/graph.py` | GraphStore |
| 12 | GET | `/app/v1/scoring/tasks/{id}` | §08, §11.5 | `api/scoring.py` (new) | ScoringEngine |
| 13 | GET | `/app/v1/scoring/tasks/{id}/history` | §08, §11.5 | `api/scoring.py` | ScoringEngine |
| 14 | POST | `/app/v1/scoring/simulate` | §08, §11.5 | `api/scoring.py` | ScoringEngine |
| 15 | GET | `/app/v1/tasks/{id}/state-history` | §08, §11.6 | `api/state.py` (new) | StateMachine |
| 16 | GET | `/app/v1/tasks/{id}/valid-transitions` | §12, §11.6 | `api/state.py` | StateMachine |
| 17 | POST | `/app/v1/tasks/{id}/transition` | §12, §11.6 | `api/state.py` | StateMachine |
| 18 | GET | `/app/v1/events` (SSE) | §10, §11.10 | `api/events.py` (new) | Redis pub/sub |

### Priority 2 — Chat + Config + Secrets

Required for the chat interface and user configuration (PRD §13, §14).

| # | Method | Path | PRD Ref | Backend Module | Dependencies |
|---|--------|------|---------|----------------|-------------|
| 19 | POST | `/app/v1/chat/messages` | §13, §11.11 | `api/chat.py` (new) | AgentLoop |
| 20 | GET | `/app/v1/chat/messages` | §13, §11.11 | `api/chat.py` | StorageClient |
| 21 | GET | `/app/v1/chat/messages/{id}` | §13, §11.11 | `api/chat.py` | StorageClient |
| 22 | WS | `/app/v1/chat/ws` | §13, §11.11 | `api/chat.py` | AgentLoop, WebSocket |
| 23 | GET | `/app/v1/config` | §14, §11.13 | `api/config.py` (new) | StorageClient |
| 24 | PUT | `/app/v1/config` | §14, §11.13 | `api/config.py` | StorageClient, Pydantic |
| 25 | PATCH | `/app/v1/config/{section}` | §14, §11.13 | `api/config.py` | StorageClient, Pydantic |
| 26 | POST | `/app/v1/secrets/{cat}/{key}` | §14, §11.12 | `api/secrets.py` (new) | SecretsClient |
| 27 | DELETE | `/app/v1/secrets/{cat}/{key}` | §14, §11.12 | `api/secrets.py` | SecretsClient |
| 28 | POST | `/app/v1/secrets/{cat}/{key}/test` | §14, §11.12 | `api/secrets.py` | SecretsClient |
| 29 | GET | `/app/v1/secrets/status` | §14, §11.12 | `api/secrets.py` | SecretsClient |

### Priority 3 — Settings + Agent Monitoring

Required for the settings panel and agent visibility (PRD §03, §05).

| # | Method | Path | PRD Ref | Backend Module | Dependencies |
|---|--------|------|---------|----------------|-------------|
| 30 | GET | `/app/v1/settings/profile` | §05, §11.3 | `api/settings.py` (extend) | GraphStore |
| 31 | PATCH | `/app/v1/settings/profile` | §05, §11.3 | `api/settings.py` | GraphStore |
| 32 | POST | `/app/v1/settings/channels/{ch}/activate` | §05, §11.3 | `api/settings.py` | ChannelAdapter |
| 33 | DELETE | `/app/v1/settings/channels/{ch}` | §05, §11.3 | `api/settings.py` | ChannelAdapter |
| 34 | GET | `/app/v1/settings/scoring-weights` | §05, §11.3 | `api/settings.py` | StorageClient |
| 35 | PATCH | `/app/v1/settings/scoring-weights` | §05, §11.3 | `api/settings.py` | StorageClient |
| 36 | GET | `/app/v1/settings/organizations` | §05, §11.3 | `api/settings.py` | GraphStore |
| 37 | POST | `/app/v1/settings/organizations` | §05, §11.3 | `api/settings.py` | GraphStore |
| 38 | PATCH | `/app/v1/settings/organizations/{id}` | §05, §11.3 | `api/settings.py` | GraphStore |
| 39 | POST | `/app/v1/settings/llm-keys` | §05, §11.3 | `api/settings.py` | SecretsClient |
| 40 | DELETE | `/app/v1/settings/llm-keys/{provider}` | §05, §11.3 | `api/settings.py` | SecretsClient |
| 41 | GET | `/app/v1/agent/status` | §03, §11.4 | `api/agent.py` (new) | AgentLoop |
| 42 | GET | `/app/v1/agent/action-queue` | §03, §11.4 | `api/agent.py` | ScoringEngine |
| 43 | GET | `/app/v1/agent/briefing` | §03, §11.4 | `api/agent.py` | BriefingGenerator |
| 44 | GET | `/app/v1/agent/triggers/schedule` | §03, §11.4 | `api/agent.py` | TriggerEngine |
| 45 | GET | `/app/v1/agent/triggers/{id}` | §03, §11.4 | `api/agent.py` | TriggerEngine |
| 46 | POST | `/app/v1/agent/triggers/{id}/fire` | §03, §11.4 | `api/agent.py` | TriggerEngine |

### Priority 4 — Skills + MCP Extensions

Additional skill and MCP endpoints (PRD §06, §07).

| # | Method | Path | PRD Ref | Backend Module | Dependencies |
|---|--------|------|---------|----------------|-------------|
| 47 | POST | `/app/v1/skills/{id}/feedback` | §06, §11.7 | `api/skills.py` (extend) | SkillRegistry |
| 48 | GET | `/app/v1/skills/workers` | §06, §11.7 | `api/skills.py` | WorkerPool |
| 49 | GET | `/app/v1/skills/{id}/executions` | §06, §11.7 | `api/skills.py` | StorageClient |
| 50 | POST | `/app/v1/skills/{id}/test` | §06, §11.7 | `api/skills.py` | SkillWorker |
| 51 | GET | `/app/v1/mcp-servers/{id}/tools` | §07, §11.8 | `api/mcp.py` (extend) | MCPClient |
| 52 | GET | `/app/v1/mcp-approvals` | §07, §11.8 | `api/mcp.py` | GraphStore |

### Priority 5 — Canvas / Agent Design

Agent workflow editor backend (PRD §04).

| # | Method | Path | PRD Ref | Backend Module | Dependencies |
|---|--------|------|---------|----------------|-------------|
| 53 | GET | `/app/v1/agents` | §04, §11.14 | `api/agents.py` (new) | StorageClient |
| 54 | POST | `/app/v1/agents` | §04, §11.14 | `api/agents.py` | StorageClient |
| 55 | GET | `/app/v1/agents/{id}` | §04, §11.14 | `api/agents.py` | StorageClient |
| 56 | PATCH | `/app/v1/agents/{id}` | §04, §11.14 | `api/agents.py` | StorageClient |
| 57 | DELETE | `/app/v1/agents/{id}` | §04, §11.14 | `api/agents.py` | StorageClient |
| 58 | GET | `/app/v1/agents/{id}/versions` | §04, §11.14 | `api/agents.py` | StorageClient |
| 59 | POST | `/app/v1/agents/{id}/test` | §04, §11.14 | `api/agents.py` | AgentLoop |

### Priority 6 — Admin Panel (All New)

Full admin API surface (PRD §09). All endpoints require `OWNER` or `ADMIN` role.

| # | Method | Path | PRD Ref | Backend Module |
|---|--------|------|---------|----------------|
| 60 | GET | `/app/v1/admin/members` | §09.2, §11.16 | `api/admin/members.py` (new) |
| 61 | POST | `/app/v1/admin/members/invite` | §09.2, §11.16 | `api/admin/members.py` |
| 62 | PATCH | `/app/v1/admin/members/{id}` | §09.2, §11.16 | `api/admin/members.py` |
| 63 | DELETE | `/app/v1/admin/members/{id}` | §09.2, §11.16 | `api/admin/members.py` |
| 64 | GET | `/app/v1/admin/features` | §09.3, §11.16 | `api/admin/features.py` (new) |
| 65 | PUT | `/app/v1/admin/features` | §09.3, §11.16 | `api/admin/features.py` |
| 66 | GET | `/app/v1/admin/features/channels` | §09.3, §11.16 | `api/admin/features.py` |
| 67 | PUT | `/app/v1/admin/features/channels` | §09.3, §11.16 | `api/admin/features.py` |
| 68 | GET | `/app/v1/admin/features/mcp-allowlist` | §09.3, §11.16 | `api/admin/features.py` |
| 69 | PUT | `/app/v1/admin/features/mcp-allowlist` | §09.3, §11.16 | `api/admin/features.py` |
| 70 | GET | `/app/v1/admin/features/marketplace` | §09.3, §11.16 | `api/admin/features.py` |
| 71 | PUT | `/app/v1/admin/features/marketplace` | §09.3, §11.16 | `api/admin/features.py` |
| 72 | GET | `/app/v1/admin/llm/providers` | §09.4, §11.16 | `api/admin/llm.py` (new) |
| 73 | PUT | `/app/v1/admin/llm/providers` | §09.4, §11.16 | `api/admin/llm.py` |
| 74 | POST | `/app/v1/admin/llm/keys` | §09.4, §11.16 | `api/admin/llm.py` |
| 75 | DELETE | `/app/v1/admin/llm/keys/{provider}` | §09.4, §11.16 | `api/admin/llm.py` |
| 76 | GET | `/app/v1/admin/llm/budget` | §09.4, §11.16 | `api/admin/llm.py` |
| 77 | PUT | `/app/v1/admin/llm/budget` | §09.4, §11.16 | `api/admin/llm.py` |
| 78 | GET | `/app/v1/admin/llm-judge/config` | §09.5, §11.16 | `api/admin/judge.py` (new) |
| 79 | PUT | `/app/v1/admin/llm-judge/config` | §09.5, §11.16 | `api/admin/judge.py` |
| 80 | GET | `/app/v1/admin/llm-judge/results` | §09.5, §11.16 | `api/admin/judge.py` |
| 81 | GET | `/app/v1/admin/llm-judge/stats` | §09.5, §11.16 | `api/admin/judge.py` |
| 82 | GET | `/app/v1/admin/guardrails` | §09.6, §11.16 | `api/admin/guardrails.py` (new) |
| 83 | PUT | `/app/v1/admin/guardrails` | §09.6, §11.16 | `api/admin/guardrails.py` |
| 84 | POST | `/app/v1/admin/guardrails/validate` | §09.6, §11.16 | `api/admin/guardrails.py` |
| 85 | POST | `/app/v1/admin/guardrails/test` | §09.6, §11.16 | `api/admin/guardrails.py` |
| 86 | GET | `/app/v1/admin/guardrails/metrics` | §09.6, §11.16 | `api/admin/guardrails.py` |
| 87 | GET | `/app/v1/admin/sso` | §09.7, §11.16 | `api/admin/sso.py` (new) |
| 88 | PUT | `/app/v1/admin/sso` | §09.7, §11.16 | `api/admin/sso.py` |
| 89 | POST | `/app/v1/admin/sso/test` | §09.7, §11.16 | `api/admin/sso.py` |
| 90 | PATCH | `/app/v1/admin/sso/enforce` | §09.7, §11.16 | `api/admin/sso.py` |
| 91 | GET | `/app/v1/admin/audit-log` | §09.9, §11.16 | `api/admin/audit.py` (new) |
| 92 | GET | `/app/v1/admin/deployment/status` | §09.10, §11.16 | `api/admin/infra.py` (new) |
| 93 | GET | `/app/v1/admin/deployment/config` | §09.10, §11.16 | `api/admin/infra.py` |
| 94 | GET | `/app/v1/admin/cluster/health` | §09.11, §11.16 | `api/admin/infra.py` |
| 95 | GET | `/app/v1/admin/backups` | §09.12, §11.16 | `api/admin/infra.py` |
| 96 | GET | `/app/v1/admin/security/status` | §09.13, §11.16 | `api/admin/infra.py` |
| 97 | GET | `/app/v1/admin/alarms` | §09.14, §11.16 | `api/admin/infra.py` |
| 98 | PATCH | `/app/v1/admin/alarms/{id}` | §09.14, §11.16 | `api/admin/infra.py` |
| 99 | GET | `/app/v1/admin/migrations` | §09.15, §11.16 | `api/admin/infra.py` |
| 100 | POST | `/app/v1/admin/migrations/apply` | §09.15, §11.16 | `api/admin/infra.py` |
| 101 | GET | `/app/v1/admin/connectors` | §09.16, §11.16 | `api/admin/connectors.py` (new) |
| 102 | POST | `/app/v1/admin/connectors` | §09.16, §11.16 | `api/admin/connectors.py` |
| 103 | POST | `/app/v1/admin/connectors/{id}/sync` | §09.16, §11.16 | `api/admin/connectors.py` |
| 104 | GET | `/app/v1/admin/connectors/{id}/health` | §09.16, §11.16 | `api/admin/connectors.py` |

---

## New Backend Modules Required

| Module | Endpoints | Description |
|--------|-----------|-------------|
| `api/graph.py` | 11 | Graph CRUD — nodes, edges, tree queries via GraphStore/AGE |
| `api/scoring.py` | 3 | Score breakdown, history, simulation via ScoringEngine |
| `api/state.py` | 3 | State history, valid transitions, manual transition via StateMachine |
| `api/events.py` | 1 | SSE event stream via Redis pub/sub |
| `api/chat.py` | 4 | Chat messages + WebSocket via AgentLoop |
| `api/config.py` | 3 | Config JSON CRUD via StorageClient |
| `api/secrets.py` | 4 | Secrets management via SecretsClient |
| `api/agent.py` | 6 | Agent status, action queue, briefing, triggers |
| `api/agents.py` | 7 | Agent definition CRUD (canvas export) |
| `api/admin/members.py` | 4 | Org member management |
| `api/admin/features.py` | 8 | Feature gating policies |
| `api/admin/llm.py` | 6 | LLM provider/model/budget config |
| `api/admin/judge.py` | 4 | LLM-as-a-Judge config + results |
| `api/admin/guardrails.py` | 5 | XML guardrail rules + metrics |
| `api/admin/sso.py` | 4 | SSO/OIDC/SAML configuration |
| `api/admin/audit.py` | 1 | Audit log query |
| `api/admin/infra.py` | 9 | Deployment, cluster, backup, security, alarms, migrations |
| `api/admin/connectors.py` | 4 | Connector CRUD + health + sync |

**Total new modules:** 18
**Total new endpoints:** 104

---

## New Backend Components Required (Non-API)

| Component | Description | PRD Ref |
|-----------|-------------|---------|
| `AgentLoop.process_chat_message()` | Single-message request-response mode for chat | §13.8 |
| `AgentResponse` model | Content + inline cards + suggested actions | §13.8 |
| `GuardedLLMClient` | Composition wrapper: LLMClient + GuardrailEngine | §09.6 |
| `GuardrailEngine` | XML rule parser + request/response evaluation | §09.6 |
| `JudgeLLMClient` | Post-response evaluator using separate LLM | §09.5 |
| `SSOService` | OIDC + SAML 2.0 authentication (extends OAuthService) | §09.7 |
| `OrgFeaturePolicy` model | Pydantic model for feature gating config | §09.3 |
| `UserConfig` model | Pydantic model for settings.json schema | §14.2 |
| SSE event publisher | Publish graph events to Redis for SSE stream | §10, §11.10 |

---

## Summary

| Priority | Category | Endpoint Count | New Modules |
|----------|----------|---------------|-------------|
| P1 | Core Cockpit (Graph + Scoring + State + Events) | 18 | 4 |
| P2 | Chat + Config + Secrets | 11 | 3 |
| P3 | Settings + Agent Monitoring | 17 | 2 |
| P4 | Skills + MCP Extensions | 6 | 0 (extend existing) |
| P5 | Canvas / Agent Design | 7 | 1 |
| P6 | Admin Panel | 45 | 8 |
| **Total** | | **104** | **18** |
