# Cockpit Backend API — Implementation Backlog

**Version:** 1.0 | **Date:** 2026-03-21 | **Status:** Draft
**Companion:** [graphclaw-cockpit PRD](https://github.com/graphclaw/graphclaw-cockpit) — `docs/prd/11-api-contract.md`

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
| 3 | `POST /auth/refresh` | Real | `auth/routes.py` | Token rotation enforced |
| 4 | `POST /auth/logout` | Real | `auth/routes.py` | Revokes refresh token |
| 5 | `GET /auth/me` | Real | `auth/routes.py` | |
| 6 | `POST /api/v1/inbound` | Real | `gateway/app.py` | |
| 7 | `POST /api/v1/outbound` | Real | `gateway/app.py` | |
| 8 | `POST /api/v1/trigger` | Real | `gateway/app.py` | |
| 9 | `GET/POST /webhooks/whatsapp` | Real | `gateway/app.py` | HMAC-SHA256 verified |
| 10 | `GET/POST /webhooks/telegram` | Real | `gateway/app.py` | Secret token header verified |
| 11 | `GET/POST /webhooks/slack` | Real | `gateway/app.py` | HMAC-SHA256 verified |
| 12 | `GET/POST /webhooks/teams` | Real | `gateway/app.py` | HMAC-SHA256 verified |
| 13 | `GET /health` | Real | `api/health.py` | |
| 14 | `GET /health/ready` | Real | `api/health.py` | |
| 15 | `GET /ready` | Real | `api/health.py` | |
| 16 | `GET /app/v1/settings` | Real | `api/settings.py` | Reads `{user_id}/config.json` via StorageClient |
| 17 | `PATCH /app/v1/settings` | Real | `api/settings.py` | Writes `{user_id}/config.json` |
| 18 | `GET /app/v1/settings/channels` | Real | `api/settings.py` | Returns live channel registry status |
| 19 | `GET /app/v1/settings/scoring-weights` | Real | `api/settings.py` | Reads `{user_id}/scoring_weights.json` |
| 20 | `PATCH /app/v1/settings/scoring-weights` | Real | `api/settings.py` | Writes scoring weights |
| 21 | `GET /app/v1/approvals` | Real | `api/approvals.py` | Queries APPROVAL nodes from GraphStore |
| 22 | `POST /app/v1/approvals/{id}/approve` | Real | `api/approvals.py` | State transition via StateMachine |
| 23 | `POST /app/v1/approvals/{id}/deny` | Real | `api/approvals.py` | State transition via StateMachine |
| 24 | `GET /app/v1/skills` | Real | `api/skill_registry.py` | SkillRegistryService query |
| 25 | `POST /app/v1/skills/install` | Real | `api/skill_registry.py` | |
| 26 | `DELETE /app/v1/skills/{id}` | Real | `api/skill_registry.py` | |
| 27 | `GET /app/v1/skills/search` | Real | `api/skill_registry.py` | |
| 28 | `GET /app/v1/skills/sources` | Real | `api/skill_registry.py` | |
| 29 | `POST /app/v1/skills/sources` | Real | `api/skill_registry.py` | |
| 30 | `DELETE /app/v1/skills/sources/{uri}` | Real | `api/skill_registry.py` | |
| 31 | `POST /app/v1/skills/{id}/feedback` | Real | `api/skill_registry.py` | Records quality score |
| 32 | `GET /app/v1/skills/workers` | Real | `api/skill_registry.py` | Worker pool status |
| 33 | `GET /app/v1/skills/{id}/executions` | Real | `api/skill_registry.py` | Execution history from storage |
| 34 | `POST /app/v1/skills/{id}/test` | Real | `api/skill_registry.py` | Submits test job |
| 35 | `GET /app/v1/mcp-servers` | Real | `api/mcp.py` | MCP registry query |
| 36 | `POST /app/v1/mcp-servers` | Real | `api/mcp.py` | |
| 37 | `PATCH /app/v1/mcp-servers/{id}` | Real | `api/mcp.py` | Trust tier update |
| 38 | `DELETE /app/v1/mcp-servers/{id}` | Real | `api/mcp.py` | |
| 39 | `GET /app/v1/mcp-servers/search` | Real | `api/mcp.py` | Official registry search |
| 40 | `GET /app/v1/mcp-servers/{id}/tools` | Real | `api/mcp.py` | |
| 41 | `GET /app/v1/mcp-approvals` | Real | `api/mcp.py` | Gated approval queue |
| 42 | `GET /app/v1/compliance/export` | Real | `compliance/` | GDPR data export |
| 43 | `POST /app/v1/compliance/erasure` | Real | `compliance/` | GDPR erasure request |
| 44 | `GET /app/v1/compliance/erasure/{id}` | Real | `compliance/` | Erasure status |
| 45 | `POST /api/v1/a2a/agents` | Real | `a2a/routes.py` | A2A agent registration |
| 46 | `GET /api/v1/a2a/agents` | Real | `a2a/routes.py` | |
| 47 | `DELETE /api/v1/a2a/agents` | Real | `a2a/routes.py` | |
| 48 | `POST /api/v1/task-update` | Real | `a2a/routes.py` | Inbound A2A status push |
| 49 | `GET /app/v1/admin/members` | Real | `api/admin/members.py` | Org member management |
| 50 | `POST /app/v1/admin/members/invite` | Real | `api/admin/members.py` | |
| 51 | `PATCH /app/v1/admin/members/{id}` | Real | `api/admin/members.py` | |
| 52 | `DELETE /app/v1/admin/members/{id}` | Real | `api/admin/members.py` | |
| 53 | `GET/PUT /app/v1/admin/features` | Real | `api/admin/features.py` | Feature gating |
| 54 | `GET/PUT /app/v1/admin/features/channels` | Real | `api/admin/features.py` | |
| 55 | `GET/PUT /app/v1/admin/llm/providers` | Real | `api/admin/llm.py` | LLM provider config |
| 56 | `POST/DELETE /app/v1/admin/llm/keys` | Real | `api/admin/llm.py` | |
| 57 | `GET/PUT /app/v1/admin/llm/budget` | Real | `api/admin/llm.py` | |
| 58 | `GET/PUT /app/v1/admin/llm-judge/config` | Real | `api/admin/judge.py` | LLM-as-Judge config |
| 59 | `GET /app/v1/admin/llm-judge/results` | Real | `api/admin/judge.py` | |
| 60 | `GET/PUT /app/v1/admin/guardrails` | Real | `api/admin/guardrails.py` | XML guardrail rules |
| 61 | `GET/PUT /app/v1/admin/sso` | Real | `api/admin/sso.py` | OIDC/SAML config |
| 62 | `GET /app/v1/admin/audit-log` | Real | `api/admin/audit.py` | Audit trail query |
| 63 | `GET /app/v1/admin/deployment/status` | Real | `api/admin/infra.py` | |
| 64 | `GET /app/v1/admin/connectors` | Real | `api/admin/connectors.py` | Connector management |

**Intelligence Hub — all implemented (Wave 7, April 2026)**

| # | Path | Status | Location |
|---|------|--------|----------|
| 65 | `GET /app/v1/intelligence/agents/{id}/profile` | Real | `api/intelligence.py` |
| 66 | `PUT /app/v1/intelligence/agents/{id}/profile` | Real | `api/intelligence.py` |
| 67 | `GET /app/v1/intelligence/agents/{id}/memory/working` | Real | `api/intelligence.py` |
| 68 | `PUT /app/v1/intelligence/agents/{id}/memory/working` | Real | `api/intelligence.py` |
| 69 | `POST /app/v1/intelligence/agents/{id}/memory/compact` | Real | `api/intelligence.py` |
| 70 | `GET /app/v1/intelligence/agents/{id}/memory/episodic` | Real | `api/intelligence.py` |
| 71 | `GET /app/v1/intelligence/agents/{id}/memory/episodic/{entry}` | Real | `api/intelligence.py` |
| 72 | `DELETE /app/v1/intelligence/agents/{id}/memory/episodic/{entry}` | Real | `api/intelligence.py` |
| 73 | `GET /app/v1/intelligence/agents/{id}/memory/semantic` | Real | `api/intelligence.py` |
| 74 | `GET /app/v1/intelligence/agents/{id}/memory/semantic/{topic}` | Real | `api/intelligence.py` |
| 75 | `PUT /app/v1/intelligence/agents/{id}/memory/semantic/{topic}` | Real | `api/intelligence.py` |
| 76 | `DELETE /app/v1/intelligence/agents/{id}/memory/semantic/{topic}` | Real | `api/intelligence.py` |
| 77 | `GET /app/v1/intelligence/skills/authored` | Real | `api/intelligence.py` |
| 78 | `POST /app/v1/intelligence/skills/authored` | Real | `api/intelligence.py` |
| 79 | `GET /app/v1/intelligence/skills/authored/{skill_id}` | Real | `api/intelligence.py` |
| 80 | `PUT /app/v1/intelligence/skills/authored/{skill_id}` | Real | `api/intelligence.py` |
| 81 | `DELETE /app/v1/intelligence/skills/authored/{skill_id}` | Real | `api/intelligence.py` |
| 82 | `POST /app/v1/intelligence/skills/authored/{skill_id}/fork` | Real | `api/intelligence.py` |
| 83 | `POST /app/v1/intelligence/skills/validate` | Real | `api/intelligence.py` |
| 84 | `POST /app/v1/intelligence/skills/import` | Real | `api/intelligence.py` |

---

## New Endpoints Required

> **Status as of Wave 7 (April 2026):** Priorities 4, 6 (admin), and the Intelligence Hub are fully implemented. Priorities 1–3 and 5 remain outstanding. The table below reflects only the remaining work.

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

### Priority 4 — Skills + MCP Extensions ✅ Complete

All skill and MCP endpoints are implemented. See the Existing Endpoints table (rows 24–41).

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

### Priority 6 — Admin Panel ✅ Complete

Full admin API surface is implemented. All endpoints require `OWNER` or `ADMIN` role. See Existing Endpoints table (rows 49–64).

---

## Remaining Backend Modules (Outstanding)

These modules do not yet exist. All admin, skills, MCP, intelligence, and auth modules are complete.

| Module | Endpoints | Description | Priority |
|--------|-----------|-------------|----------|
| `api/graph.py` | 11 | Graph CRUD — nodes, edges, tree queries via GraphStore/AGE | P1 |
| `api/scoring.py` | 3 | Score breakdown, history, simulation via ScoringEngine | P1 |
| `api/state.py` | 3 | State history, valid transitions, manual transition via StateMachine | P1 |
| `api/events.py` | 1 | SSE event stream via Redis pub/sub | P1 |
| `api/chat.py` | 4 | Chat messages + WebSocket via AgentLoop | P2 |
| `api/config.py` | 3 | Config JSON CRUD via StorageClient | P2 |
| `api/secrets.py` | 4 | Secrets management via SecretsClient | P2 |
| `api/agent.py` | 6 | Agent status, action queue, briefing, triggers | P3 |
| `api/agents.py` | 7 | Agent definition CRUD (canvas export) | P5 |

**Remaining endpoints:** 42 across 9 modules

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

| Priority | Category | Endpoint Count | Status |
|----------|----------|---------------|--------|
| P1 | Core Cockpit (Graph + Scoring + State + Events) | 18 | **Outstanding** |
| P2 | Chat + Config + Secrets | 11 | **Outstanding** |
| P3 | Settings + Agent Monitoring | 17 | **Outstanding** |
| P4 | Skills + MCP Extensions | 6 | ✅ Complete |
| P5 | Canvas / Agent Design | 7 | **Outstanding** |
| P6 | Admin Panel | 45 | ✅ Complete |
| P7 | Intelligence Hub | 20 | ✅ Complete (Wave 7) |
| **Total** | | **124** | **42 remaining** |
