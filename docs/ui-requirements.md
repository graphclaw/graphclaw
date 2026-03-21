# GraphClaw Web UI — Requirements

> **Note:** This document covers the Web UI as a SEPARATE PROJECT. The UI is not part of the graphclaw Python package. It will have its own GitHub repository.

**Source:** Extracted from `docs/task-graph-requirements.md` (v1.1)
**Created:** 2026-03-20
**Status:** Draft

---

## Table of Contents

1. [Overview](#1-overview)
2. [Graph Visualization Requirements](#2-graph-visualization-requirements)
3. [Settings Panel Requirements](#3-settings-panel-requirements)
4. [Skill Marketplace UI Requirements](#4-skill-marketplace-ui-requirements)
5. [MCP Registry UI Requirements](#5-mcp-registry-ui-requirements)
6. [Explainability Dashboard Requirements](#6-explainability-dashboard-requirements)
7. [Technical Constraints](#7-technical-constraints)
8. [API Contract Requirements](#8-api-contract-requirements)

---

## 1. Overview

The GraphClaw Web UI is a **separate React project** with its own GitHub repository, separate from the `graphclaw` Python package. It is a companion interface to the primary conversational channel experience (WhatsApp / Telegram / Email).

### 1.1 Design Philosophy

The agent must be fully useful without the user ever opening the visual interface. The web UI is a power-user complement, not a requirement for daily use.

```
Surface 1: Conversational Channel (primary, daily use)
  WhatsApp / Telegram / Email
  -> Daily briefings, quick decisions, task creation, status updates
  -> No app required — agent comes to the user
  -> Design principle: the agent must be fully useful
     without the user ever opening a visual interface

Surface 2: Visual Graph Interface (power use, weekly)
  Web / Mobile app
  -> Review and edit graph structure
  -> Planning sessions and project decomposition
  -> Dependency visualization
  -> Skill agent and settings management
  -> Complement to the channel, not a requirement

Surface 3: Settings Panel (one-time + occasional)
  -> Channel configuration and verification
  -> Organization workspace setup
  -> Skill agent library management
  -> LLM provider configuration
  -> Scoring weight adjustment (power users)
```

### 1.2 Three Interface Surfaces

The web app covers Surface 2 (Visual Graph Interface) and Surface 3 (Settings Panel). Surface 1 (Conversational Channel) is handled entirely by the GraphClaw backend.

### 1.3 Sync Principle

All actions on any surface immediately reflect in the graph. The conversational agent always has current state regardless of where the last action happened.

### 1.4 Mobile vs. Desktop Split

**Mobile (primary for quick decisions):**
- Visual app optimized for MY TASKS VIEW
- Quick approve / snooze / delegate gestures
- Voice note support for longer updates (future)

**Desktop (primary for planning and graph editing):**
- Full graph visualization with zoom and pan
- Multi-task bulk operations
- Skill agent management and SKILL.md editing
- Side-by-side: graph view + conversation thread

---

## 2. Graph Visualization Requirements

### 2.1 Key Views

The graph interface must support five primary views:

| View | Description |
|------|-------------|
| **GOAL VIEW** | Zoom out: all goals, milestone progress bars. Entry point for weekly planning session. |
| **PROJECT VIEW** | One goal expanded, full task tree visible. Critical path highlighted. Sequential chains visually distinct from parallel. |
| **MY TASKS VIEW** | Flat list: tasks assigned to or owned by me, sorted by computed priority score. ScoreExplanation visible on hover/tap. |
| **RESOURCE VIEW** | All tasks grouped by assignee. Capacity bar per resource (load_factor visualization). At-risk resources highlighted. |
| **TIMELINE VIEW** | Gantt-style: tasks plotted against deadlines. Critical path in distinct color. Constraint nodes shown as boundary markers. |

### 2.2 Visual Language

```
Node color     = state
  Active       -> blue
  In Progress  -> green
  Blocked      -> red
  Delayed      -> amber
  Complete     -> grey
  Snoozed      -> light grey

Node size      = priority score (larger = higher priority)
Edge thickness = dependency strength (hard vs. soft)
Critical path  = highlighted in accent color
Org color tags = distinguish workspace nodes visually
```

Organization workspaces carry `color_tag` and `emoji_tag` fields on `OrganizationNode` for visual distinction across the graph.

### 2.3 Key Interactions

```
Drag node          -> reassign to different resource
Click node         -> see ScoreExplanation ("why is this ranked here?")
Inline edit        -> update status, deadline, or description in-context
Click empty space  -> create new task node at that level of the hierarchy
Approve/reject     -> pending decisions resolved without leaving the graph
Bulk select        -> multi-task reassign, defer, or priority change
```

### 2.4 Zoom and Filter Controls

- Zoom out to Goal-level overview (all goals, milestone progress bars)
- Zoom into a single Goal to see the full task tree
- Filter by: goal, status, assignee, organization workspace
- Critical path toggle: highlight / hide critical path nodes and edges

### 2.5 Graph Visualization Library

The implementation must use either:
- **Cytoscape.js** (`cytoscape/cytoscape.js`) — mature graph rendering with zoom, pan, layout algorithms, and hover interaction support
- **React Flow** (`xyflow/xyflow`) — React-native node-based UI, alternative for a more React-idiomatic implementation

The specific choice between Cytoscape.js and React Flow is a technical decision for the UI project. Both are listed as Phase 4 library accelerators in `build-plan.md`.

### 2.6 Task Node Detail Panel

When a user clicks a task node, the side panel must show:
- Task title, description, state, assigned resource
- Priority score (computed_priority) with ScoreExplanation breakdown (W1–W7 factor scores)
- Timeline: deadline, estimated effort, actual effort, completed_at
- Progress: percentage, confidence, last_update, completion_signal
- Inbound update log (last N entries)
- Human override controls (prioritize, deprioritize, snooze)
- Artifacts (if any) with download links
- Autonomy settings (per-node override of global defaults)

### 2.7 Organization Workspace Switcher

- Top-level selector to switch between organization workspaces (Personal, Work, Side Project, etc.)
- Unified cross-org view available as a pull-based option (never mixed into a single briefing unprompted)
- Each org uses its `color_tag` and `emoji_tag` for visual identity

---

## 3. Settings Panel Requirements

> **Note:** The settings panel UI is part of the separate Web UI project (docs/ui-requirements.md). The GraphClaw backend exposes /app/v1/ REST API endpoints that the UI consumes.

### 3.1 Channel Configuration (One-Time Onboarding)

The settings panel handles channel authentication, configured once during onboarding. The user never touches authentication internals — the platform handles all credential management.

**WhatsApp activation flow:**
```
-> Platform provisions a WhatsApp Business number for this user
-> User sends "ACTIVATE [code]" to that number from their WhatsApp
-> Platform receives message, verifies sender phone number
-> Links phone number to UserNode
-> User saves the agent number as a contact
```

**Telegram activation flow:**
```
-> Platform shows user their personal bot handle: @jd_workgraph_bot
-> User opens Telegram, finds the handle, sends /start
-> Bot records Telegram user_id, links to UserNode
-> User saves the bot as a contact
```

**Email:**
```
-> No user-side setup required
-> Agent email shown: jd-agent@workgraph.app
-> Platform configures DKIM/SPF at domain level
-> User saves agent email as a contact
```

**Per-org channel binding:**
```
Step 5: Set preferred channel and per-org channel bindings
  -> "Which channel should your agent use for outbound briefings?"
  -> Optionally bind different channels to different organizations
```

### 3.2 Organization Workspace Setup

- Create, rename, and configure Organization Workspaces (Personal, Work, Side Project, Custom)
- Set per-org briefing schedule: channel, time, days, style (concise/detailed), timezone
- Assign `color_tag` and `emoji_tag` for visual identity
- Configure per-org resource lists and permitted channels
- Set data isolation flags: `data_isolated`, `contact_isolated`, `channel_isolated`

### 3.3 LLM Provider Configuration

- Select LLM provider: Anthropic, OpenAI, Google, or custom (via LiteLLM)
- Configure model preference (specific model or "fast" / "best" aliases)
- BYOK (Bring Your Own Key) flow: user submits API key via `POST /app/v1/settings/llm-keys`; key stored in Secrets Manager, never in the UI or logs

```
STEP 1: User configures BYOK API key in settings panel
  Browser -> POST /app/v1/settings/llm-keys
             Authorization: Bearer [platform JWT] (httpOnly cookie)
```

Agents use the BYOK key retrieved from Secrets Manager at invocation time. The settings panel shows only the key reference ID, never the plaintext key.

Key rotation: user rotates key in settings panel — new key stored in Secrets Manager, running container cache expires within 15 minutes.

Agent model/config upgrade:
```
User updates assets.md via settings panel
Next invocation uses new LLM config
No agent restart required — config is read fresh each invocation
```

### 3.4 Briefing Schedule Configuration

Users configure briefing schedules in the settings panel. Stored in the relational DB and read by the cron scheduler.

Fields per organization:
- Channel: whatsapp | telegram | email
- Time: HH:MM local time
- Days: MON–SUN selection
- Style: concise | detailed
- Timezone

### 3.5 Scoring Weight Adjustment (Power Users)

The settings panel exposes W1–W7 scoring weights for power users who want to tune the agent's prioritization behavior. Changes are applied to `UserNode.scoring_weights` via the `/app/v1/settings/scoring-weights` endpoint.

| Weight | Factor | Default |
|--------|--------|---------|
| W1 | Timeline Urgency | 0.25 |
| W2 | Dependency Weight | 0.20 |
| W3 | Critical Path | 0.20 |
| W4 | Blocker | 0.15 |
| W5 | Human Override | 0.10 |
| W6 | Resource Risk | 0.05 |
| W7 | Constraint Pressure | 0.05 |

### 3.6 Trigger Engine Configuration

The trigger engine is configurable from the settings panel. Users can adjust:
- Scheduled briefing times and cadence (per org)
- Follow-up timing defaults (`default_follow_up_days`)
- Interrupt threshold (`interrupt_threshold`): urgency score that justifies a mid-day alert (max 2 per day)
- Autonomy defaults: `auto_update_ai_agents`, `auto_send_followups`, `auto_close_resolved`

### 3.7 A2A API Key Management

For external AI agents registered as ResourceNodes:
- Generate a new `wg_agent_` API key (shown once, never again)
- Rotate an existing key (old key invalid immediately)
- Revoke a key (hash cleared from ResourceNode)

The settings panel calls `POST /app/v1/a2a-keys`, `PUT /app/v1/a2a-keys/{resource_id}/rotate`, `DELETE /app/v1/a2a-keys/{resource_id}`.

---

## 4. Skill Marketplace UI Requirements

### 4.1 Skill Library View

The settings panel includes a skill agent management section:
- List of installed skills (user-defined + system-provided)
- Per-skill: skill_id, name, version, trigger_types, org_scope, output_type, usage_count, avg_quality_score
- Enable / disable per skill
- View / edit SKILL.md content in a code editor (desktop only)
- Fork a system skill into a user-defined skill (conversational path also supported)
- Delete a user-defined skill (system skills cannot be deleted)

### 4.2 Skill Registry v2 (Phase 4)

The Phase 4 Skill Registry adds remote sources:
- Browse a remote GitHub-hosted skills registry (`marketplace.json`)
- Search by name, trigger type, or tag
- Install a skill (copies SKILL.md into user's `/skills/user/` directory)
- Uninstall a skill
- Pin to a specific version (version pinning supported)
- View changelog between versions

### 4.3 Skill Invocation Configuration

Per-skill settings accessible from the UI:
- Override LLM provider and model for a specific skill
- Set `output_type`: DRAFT_FOR_REVIEW or AUTO_COMPLETE
- Set `requires_approval` toggle
- Set `org_scope` (which organizations the skill is active in)

### 4.4 Skill Quality Feedback

After a skill-assisted task completes, the UI surfaces a lightweight feedback prompt:
- Thumbs up / thumbs down on the skill output
- Optional free-text comment
- Feedback updates `avg_quality_score` on the skill registry entry

---

## 5. MCP Registry UI Requirements

> **Note:** The Web UI components for MCP server management (search, install, trust tier configuration) are documented in docs/ui-requirements.md as part of the separate UI project. The GraphClaw backend exposes /app/v1/mcp-registry REST endpoints that the UI consumes.

### 5.1 MCP Server List

The MCP Registry section of the settings panel shows each user's registered MCP servers (`MCPServerNode` records):

| Field | Description |
|-------|-------------|
| name | Human-readable name (e.g. "Google Calendar") |
| transport | stdio / sse / http |
| endpoint_url | URL (for sse/http transports) |
| trust_tier | AUTO / GATED / BLOCKED |
| scope | Declared capability scopes |
| enabled | On/Off toggle |
| registered_at | Registration timestamp |
| last_used_at | Last tool call timestamp |

### 5.2 Pre-Built MCP Server Adapters

The platform ships pre-built configuration templates. Users activate them from the settings panel — no command-line setup required.

| Service | Transport | Trust Default | Key Capabilities |
|---------|-----------|---------------|-----------------|
| Google Calendar | SSE | AUTO (read) / GATED (write) | Read events, create events, check free/busy |
| GitHub | HTTP | AUTO (read) / GATED (write) | List issues/PRs, read file, create issue, add comment |
| Slack | HTTP | AUTO (read) / GATED (write) | Read channel messages, post message, list channels |
| Jira | HTTP | AUTO (read) / GATED (write) | List issues, update status, add comment |
| Notion | HTTP | AUTO (read) / GATED (write) | Read pages/databases, create page |
| Linear | HTTP | AUTO (read) / GATED (write) | List issues, update status |
| Google Drive | HTTP | AUTO (read) / GATED (write) | List files, read file content, create doc |

### 5.3 Official Registry Search

The UI integrates a search interface against the official MCP registry at `registry.modelcontextprotocol.io`:
- Search by name or capability
- View server metadata: transport type, declared scopes, publisher
- One-click install: user provides endpoint URL / command + credentials, UI calls `POST /app/v1/mcp-registry`

### 5.4 Custom MCP Server Registration

Custom MCP servers (user-built or third-party) can be registered by providing:
- Transport type: stdio / sse / http
- Endpoint URL (for sse/http) or command (for stdio)
- OAuth token or API key (stored in Secrets Manager, never in the UI)
- Initial trust tier: AUTO / GATED / BLOCKED

### 5.5 Trust Tier Configuration

Per-server trust tier controls:
- **AUTO** — tools from this server are called without user confirmation. Suitable for read-only tools the user has explicitly trusted.
- **GATED** — the orchestrating agent proposes the tool call and waits for user approval before executing. Suitable for write operations.
- **BLOCKED** — server is registered but all tool calls are rejected. Used to temporarily suspend a server without removing its configuration.

The trust tier can be changed at any time from the settings panel. Any tier can be revoked instantly (Design Principle 60).

### 5.6 GATED Approval Workflow (in-app)

When a GATED MCP server tool call is pending user approval:
- The UI surfaces an approval card: server name, tool name, proposed input parameters
- User approves or rejects
- Approval resolves the pending APPROVAL task node in the graph
- If the user has not responded within the session, the agent notifies via channel (WhatsApp / Telegram / Email)

### 5.7 MCP Scope Visualization

For each registered server, the UI displays:
- Declared capability scopes (e.g. `read_events`, `create_event`, `get_free_busy`)
- Which scopes are read-only vs. write-capable (write scopes highlighted in amber)
- Last tool call: tool name, timestamp, trust tier used

---

## 6. Explainability Dashboard Requirements

### 6.1 Principle

Explainability is the default interface. The agent can always answer "why is this at the top?" in natural language. The human never experiences the system as a black box.

- **Default mode:** Agent explains decisions in natural language on request (via channel)
- **Power user mode:** Scoring weights W1–W7 are visible and adjustable (settings panel)
- **Audit mode:** Full ScoreExplanation records are stored and queryable for any node

### 6.2 ScoreExplanation Panel

When a user clicks a task node, the ScoreExplanation panel shows:
- Computed priority score (final weighted total)
- Per-factor breakdown: W1 (timeline urgency), W2 (dependency weight), W3 (critical path), W4 (blocker), W5 (human override), W6 (resource risk), W7 (constraint pressure)
- `on_critical_path` flag with visual indicator
- `chain_urgency_rollup` — urgency contribution from downstream nodes
- `score_reasoning` — natural language explanation written by the agent at scoring time
- `last_scored_at` — timestamp of last scoring pass

Example display:
```
TSK-4821 is ranked #1 for three reasons:

  1. Critical path: It is on the critical path for your Q3 Launch goal
     (P1 priority), which applies a 1.5x multiplier to its base score.

  2. Tight deadline: The deadline is in 3 days and estimated effort is
     2 days — almost no slack remaining (urgency score: 0.85).

  3. Chain position: This is the first node in a sequential chain of
     4 tasks. 'Deploy to production' at the end of the chain is due
     tomorrow, so that urgency has rolled back here.

  Additionally, Alex (assigned) has not responded to the last follow-up,
  which elevates the resource risk score.
```

### 6.3 Score History

The explainability dashboard shows `ScoreExplanation` records over time for any task node:
- Timeline of score changes (with timestamps)
- What triggered each re-score (state change, inbound update, time-based)
- Delta view: which factors changed and by how much

The `score_explanation` table in Postgres is the data source. Written at every scoring pass, it powers the explainability interface without requiring the agent to reason from scratch at query time.

### 6.4 Decision Audit Trail

For DECISION task nodes and APPROVAL task nodes:
- Full history of options considered
- Which branch was activated or pruned on resolution
- Who resolved it (AGENT / HUMAN) and when
- `changed_by` field from `StateHistoryEntry` records

### 6.5 Resource Reliability View

In the RESOURCE VIEW, each resource card shows reliability metrics:
- `overall_score` (0.0–1.0)
- `on_time_delivery_rate`
- `proactive_update_rate`
- `response_rate`
- `avg_response_time_hrs`
- Active risk signals with expiry times

### 6.6 Behavioral Model Transparency (Power Users)

For power users, the UI exposes the behavioral model the agent has built for the user:
- `avg_estimate_accuracy`
- `preferred_task_batch_size`
- `responsive_hours`
- `decision_speed`
- `override_frequency`
- Confidence milestones (5, 10, 20, 30, 60 briefing cycles)

---

## 7. Technical Constraints

### 7.1 Framework

- **React** (latest stable) — the UI is a React single-page application
- **TypeScript** — all UI code must be typed
- Separate GitHub repository from `graphclaw` Python package

### 7.2 Graph Visualization

Choose one:
- **Cytoscape.js** (`cytoscape/cytoscape.js`) — interactive task graph rendering, mature layout algorithms, hover and click interaction
- **React Flow** (`xyflow/xyflow`) — React-native node-based UI, better DX for React-idiomatic development

Both are listed as Phase 4 library accelerators in `build-plan.md`.

### 7.3 UI Component Library

- **shadcn/ui** — Radix UI primitives + Tailwind CSS. Used for all non-graph UI: settings panels, modals, forms, tables, cards, toasts.

### 7.4 Data Fetching

- **TanStack Query** (`@tanstack/react-query`) — server state management. All API calls go through TanStack Query for caching, background refresh, optimistic updates, and error handling.

### 7.5 Authentication

- Platform JWT (RS256, 15-minute expiry) issued after OAuth 2.0 (Google / Microsoft / GitHub) login
- JWT delivered as `httpOnly`, `Secure`, `SameSite=Strict` cookie — never accessible via JavaScript
- Refresh token (opaque, 256-bit random) in a separate `httpOnly` cookie
- The UI initiates the OAuth flow; the GraphClaw backend handles token exchange and JWT issuance

```
1. User visits the settings panel or graph UI (unauthenticated)
2. Browser redirected to IdP authorization endpoint
   ...
10. Platform JWT -> browser as httpOnly, Secure, SameSite=Strict cookie
    Refresh token (opaque, 256-bit random) -> separate httpOnly cookie
```

### 7.6 Real-Time Updates

- The UI must poll or subscribe to graph state changes to reflect agent actions in real time
- Recommended: Server-Sent Events (SSE) from the `/app/v1/events` stream, or WebSocket for lower latency
- TanStack Query background refetch is acceptable for lower-frequency views (Settings, Skill Registry)

### 7.7 Security Constraints

- All API calls use the platform JWT cookie (no manual Authorization header management needed)
- Sensitive data (API keys, channel tokens, LLM keys) is never displayed in full — only masked or reference IDs are shown
- The UI must not store any secrets in `localStorage` or `sessionStorage`
- CSRF protection: all mutating requests include an `X-CSRF-Token` header (or use SameSite=Strict cookie policy)

---

## 8. API Contract Requirements

The Web UI consumes the `/app/v1/` router exposed by the GraphClaw gateway container. All endpoints require a valid platform JWT.

### 8.1 Graph API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/graph/goals` | List all GoalNodes for the authenticated user |
| GET | `/app/v1/graph/goals/{goal_id}/tree` | Full task tree for a Goal (nodes + edges) |
| GET | `/app/v1/graph/tasks` | Paginated task list (filterable by state, assignee, org) |
| GET | `/app/v1/graph/tasks/{task_id}` | Full TaskNode detail including ScoreExplanation |
| PATCH | `/app/v1/graph/tasks/{task_id}` | Update task state, deadline, description, override |
| POST | `/app/v1/graph/tasks` | Create a new task node |
| GET | `/app/v1/graph/resources` | List all ResourceNodes for the user |
| GET | `/app/v1/graph/score-explanations/{task_id}` | ScoreExplanation history for a task |

### 8.2 Settings API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/settings/profile` | Get UserNode preferences and behavioral model |
| PATCH | `/app/v1/settings/profile` | Update preferences (briefing style, timezone, working hours) |
| GET | `/app/v1/settings/channels` | Get channel configuration |
| POST | `/app/v1/settings/channels/{channel}/activate` | Initiate channel activation flow |
| DELETE | `/app/v1/settings/channels/{channel}` | Deactivate a channel |
| GET | `/app/v1/settings/scoring-weights` | Get W1–W7 scoring weights |
| PATCH | `/app/v1/settings/scoring-weights` | Update scoring weights |
| GET | `/app/v1/settings/organizations` | List OrganizationNodes |
| POST | `/app/v1/settings/organizations` | Create a new organization workspace |
| PATCH | `/app/v1/settings/organizations/{org_id}` | Update org settings (briefing schedule, channels, color) |
| POST | `/app/v1/settings/llm-keys` | Store BYOK LLM API key in Secrets Manager |
| DELETE | `/app/v1/settings/llm-keys/{provider}` | Remove BYOK key |

### 8.3 Skill Registry API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/skills` | List installed skills (user + system) |
| GET | `/app/v1/skills/{skill_id}` | Get skill detail including SKILL.md content |
| POST | `/app/v1/skills` | Install a skill (from marketplace or custom upload) |
| PATCH | `/app/v1/skills/{skill_id}` | Update skill config (LLM override, output_type, org_scope) |
| DELETE | `/app/v1/skills/{skill_id}` | Uninstall a user-defined skill |
| GET | `/app/v1/skills/marketplace` | Browse remote marketplace registry (marketplace.json) |
| POST | `/app/v1/skills/{skill_id}/feedback` | Submit quality feedback (updates avg_quality_score) |

### 8.4 MCP Registry API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/mcp-registry` | List all MCPServerNode records for the user |
| GET | `/app/v1/mcp-registry/{server_id}` | Get MCPServerNode detail |
| POST | `/app/v1/mcp-registry` | Register a new MCP server |
| PATCH | `/app/v1/mcp-registry/{server_id}` | Update trust tier, enabled, endpoint_url |
| DELETE | `/app/v1/mcp-registry/{server_id}` | Remove an MCP server registration |
| GET | `/app/v1/mcp-registry/search` | Search official registry.modelcontextprotocol.io |
| GET | `/app/v1/mcp-registry/approvals` | List pending GATED approval tasks |
| POST | `/app/v1/mcp-registry/approvals/{task_id}/approve` | Approve a GATED tool call |
| POST | `/app/v1/mcp-registry/approvals/{task_id}/reject` | Reject a GATED tool call |

### 8.5 A2A API Key Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/a2a-keys` | List all registered external agent keys (hashed, no plaintext) |
| POST | `/app/v1/a2a-keys` | Generate a new `wg_agent_` API key for a ResourceNode |
| PUT | `/app/v1/a2a-keys/{resource_id}/rotate` | Rotate key (old key invalid immediately) |
| DELETE | `/app/v1/a2a-keys/{resource_id}` | Revoke key (hash cleared from ResourceNode) |

### 8.6 Auth API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/auth/login/{provider}` | Initiate OAuth 2.0 flow (provider: google, microsoft, github) |
| GET | `/app/v1/auth/callback/{provider}` | OAuth callback — exchanges code for JWT, sets cookies |
| POST | `/app/v1/auth/refresh` | Refresh platform JWT using refresh token cookie |
| POST | `/app/v1/auth/logout` | Revoke JWT (jti added to Redis revocation list), clear cookies |

### 8.7 Real-Time Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/app/v1/events` | SSE stream of graph state change events for the authenticated user |

Event types on the SSE stream:
- `task.state_changed` — a task transitioned to a new state
- `task.scored` — a task was re-scored (with new priority value)
- `briefing.ready` — a daily briefing has been generated
- `approval.pending` — a new GATED MCP tool call awaits user approval
- `skill.completed` — a skill agent completed a task
