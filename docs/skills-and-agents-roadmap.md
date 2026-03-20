# Skills, Agents, and Channels Roadmap

This document tracks the full roadmap of skill agents, orchestrating agent capabilities, and channel adapters across all 5 build phases.

## Skill Agents

| Skill Agent | Phase | Status | Purpose | LLM | Category |
|-------------|-------|--------|---------|-----|----------|
| Research Agent | 1 | Done | Web search + summarization for Research tasks | Claude + Tavily | Info Gathering |
| Email Drafter | 1 | Done | Compose emails from task context + templates | Claude | Composition |
| Report Writer | 1 | Done | Weekly/monthly report generation from graph data | Claude | Synthesis |
| Meeting Notes Agent | 2 | Planned | Transcribe + structure meeting notes | Whisper + Claude | Audio Processing |
| LinkedIn Outreach Agent | 3 | Planned | Draft personalized outreach messages from profile data | Claude | Outreach |
| Pipeline Report Agent | 3 | Planned | Aggregate prospect status for BD reporting | Graph query + Claude | Analytics |
| Calendar Sync Agent | 4 | Planned | Bi-directional Google/Outlook calendar awareness | Google/Outlook APIs | Integration |
| Import Agent | 4 | Planned | Parse Jira/Asana/Notion exports into graph nodes | Per-adapter + Claude | Data Import |
| Monitoring Agent | 5 | Planned | Infrastructure health monitoring + anomaly alerts | Prometheus + AlertManager | Ops |

## Channel Adapters

| Channel | Phase | Status | Protocol | Auth Method |
|---------|-------|--------|----------|-------------|
| Email (IMAP/SMTP) | 1 | Done | Polling | SPF/DKIM |
| WhatsApp Business | 2 | Planned | Webhook | HMAC-SHA256 |
| Telegram Bot | 2 | Planned | Webhook | Bot token header |
| Slack | 5 | Planned | Webhook (Events API) | OAuth 2.0 (Bolt SDK) |
| Microsoft Teams | 5 | Planned | Webhook (Activity Feed) | OAuth 2.0 (MS Graph) |

## Orchestrating Agent Evolution

| Aspect | Phase 1 (done) | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|--------|----------------|---------|---------|---------|---------|
| Users | Single | Single (org-aware) | Multi-user | Multi-user | Enterprise |
| Channels | Email | +WhatsApp, Telegram | — | +Web UI | +Slack, Teams |
| LLM calls | None (scoring only) | Basic reasoning | Delegation reasoning | Marketplace queries | Compliance-aware |
| Delegation | Self-only | Self-only | Cross-user + A2A | A2A + marketplace | Cross-org |
| Scoring learning | None | Override tracking | EMA weight learning | EMA weights | EMA + audit |
| Auth | None | None | OAuth 2.0 + JWT | JWT | JWT + GDPR |
| Visibility | All tasks | Org-scoped | Node-level ACLs | ACLs | ACLs + audit trail |

## LLM Provider Support

| Provider | Phase | Status | Use Case |
|----------|-------|--------|----------|
| LiteLLM (proxy) | 1 | Done | Default — wraps 100+ providers |
| Anthropic SDK | 1 | Done | Direct Claude access (orchestrator) |
| OpenAI SDK | 1 | Done | GPT-4o option for skills |
| Ollama (local) | 2 | Planned | Cost-free dev/test environment |
| AWS Bedrock | 3 | Planned | Enterprise compliance (data residency) |
| BYOK (user key) | 3 | Planned | Users bring their own API keys |

## Phase 2 Detail: Multi-Channel + Organizations (Wks 13-20)

**New skill agents:**
- Meeting Notes Agent: Receive audio/transcript → Whisper transcription → Claude structuring → Task nodes

**New channel adapters:**
- WhatsApp Business API: HMAC signature verification, message templates, media handling
- Telegram Bot API: python-telegram-bot, inline keyboards, file handling

**Infra additions:**
- Conversation context cache (Redis) — per-channel conversation threading
- Channel switching — user can move between channels mid-conversation
- Organization workspaces — org node + workspace isolation + member management
- Alias resolution — map channel-specific IDs to graph UserNodes

## Phase 3 Detail: Multi-User + Security + A2A (Wks 21-28)

**New skill agents:**
- LinkedIn Outreach Agent: Profile data + task context → personalized outreach drafts
- Pipeline Report Agent: Graph query for prospect/client tasks → BD pipeline summary

**New agent type:**
- A2A REST API: External agents can read/write tasks via scoped API keys (Section 20 of PRD)

**Auth stack:**
- OAuth 2.0 + PKCE: Google, Microsoft, GitHub IdPs
- Platform JWT: RS256, 15-min expiry, refresh token rotation, Redis jti revocation
- IAM per-container: User-scoped S3 prefix conditions
- Multi-tenant: Container-per-user with idle-to-zero scaling

## Phase 4 Detail: Visual Interface + Advanced Skills (Wks 29-36)

**New skill agents:**
- Calendar Sync Agent: Google Calendar + Outlook bi-directional sync → deadline nodes
- Import Agent: Jira/Asana/Notion export parsers → bulk graph node creation

**Web UI:**
- React application with Cytoscape.js or react-flow graph visualization
- Task management panel, settings, briefing viewer
- REST/GraphQL API server

**Skill marketplace:**
- Registry with versioning
- Community skill contributions (via SKILL.md format)

## Phase 5 Detail: Enterprise + Observability (Wks 37-48)

**New skill agents:**
- Monitoring Agent: Prometheus metrics + AlertManager → infrastructure health tasks

**New channels:**
- Slack: Bolt SDK, OAuth 2.0, app home, slash commands
- Microsoft Teams: MS Graph SDK, adaptive cards, activity feed

**Compliance + scale:**
- GDPR: Right-to-erasure, PII anonymization, data export
- SOC 2: Audit trail for all state transitions
- CloudWatch: Per-user log groups, metric filters, cost monitoring, dashboards
- Rolling deployments + schema migration

## SKILL.md Format

All skill agents are defined using the SKILL.md format (parsed by `src/graphclaw/skills/parser.py`):

```yaml
---
skill_id: email-drafter
skill_name: Email Drafter
version: 1.0.0
llm_provider: any
model: claude-sonnet-4-6
max_tokens: 2048
temperature: 0.3
timeout_seconds: 30
---

# System Prompt

You are an expert email writer...

## Task

Draft a professional email for the following task context:
{{task_context}}
```

To contribute a new skill: create a `SKILL.md` file in `src/graphclaw/skills/definitions/` following the format above.

## Agent-to-Agent (A2A) Protocol

GraphClaw will implement Google's open A2A protocol (Phase 3) for interoperability with external agents. External agents can:
- Query the task graph (scoped by API key)
- Create tasks and follow-up requests
- Receive completion notifications via webhooks

MCP (Model Context Protocol) tool exposure is also planned for Phase 3, allowing agents to consume GraphClaw capabilities as MCP tools.
