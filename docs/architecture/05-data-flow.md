# 05 — Data Flow & UML Sequence Diagrams

---

## 1. Inbound Message Lifecycle
*(Email / Slack / Teams → Task update in graph)*

```mermaid
sequenceDiagram
    participant CH as External Channel<br/>(Email·Slack·Teams)
    participant ADPT as ChannelAdapter<br/>(normalize + verify)
    participant BROKER as Redis Broker<br/>INBOUND_MESSAGES queue
    participant PROC as InboundProcessor<br/>(consumer)
    participant LLM as LLMClient<br/>(Claude / GPT)
    participant DB as AgeGraphStore<br/>(PostgreSQL+AGE)
    participant SSE as SSE Event Stream<br/>/app/v1/events

    CH->>ADPT: raw webhook payload / IMAP poll
    ADPT->>ADPT: verify_signature()
    ADPT->>ADPT: normalize() → InboundMessage
    ADPT->>BROKER: publish(INBOUND_MESSAGES, msg.json())
    BROKER-->>PROC: consume message
    PROC->>DB: vector_search(msg.embedding) → candidate tasks
    PROC->>LLM: classify intent + extract task update
    LLM-->>PROC: structured update (task_id, new_state, progress)
    PROC->>DB: update_node(task_id, props)
    PROC->>DB: append StateHistoryEntry
    PROC->>SSE: emit TaskUpdatedEvent
    SSE-->>CH: (optional) trigger outbound reply
```

---

## 2. Outbound Message Delivery
*(Agent reply → Channel delivery)*

```mermaid
sequenceDiagram
    participant AGENT as Agent / API Route
    participant API as POST /api/v1/outbound/messages
    participant BROKER as Redis Broker<br/>OUTBOUND_MESSAGES queue
    participant ROUTER as Channel Router<br/>(consumer)
    participant EMAIL as EmailSender
    participant SLACK as SlackSender
    participant TEAMS as TeamsSender

    AGENT->>API: OutboundMessage{channel, recipient, body}
    API->>BROKER: publish(OUTBOUND_MESSAGES, msg.json())
    API-->>AGENT: 202 Accepted {status: queued}
    BROKER-->>ROUTER: consume message
    ROUTER->>ROUTER: route by msg.channel
    alt channel == "email"
        ROUTER->>EMAIL: send(message)
        EMAIL->>EMAIL: aiosmtplib.sendmail()
    else channel == "slack"
        ROUTER->>SLACK: send(message)
        SLACK->>SLACK: POST /api/chat.postMessage
    else channel == "teams"
        ROUTER->>TEAMS: send(message)
        TEAMS->>TEAMS: POST webhook_url
    end
```

---

## 3. OAuth 2.0 Login Flow

```mermaid
sequenceDiagram
    actor User
    participant GW as Gateway /auth/login
    participant IDP as Identity Provider<br/>(Google / GitHub / MS)
    participant CB as Gateway /auth/callback
    participant JWT as JWTService
    participant DB as AgeGraphStore

    User->>GW: GET /auth/login?provider=google
    GW->>GW: OAuthService.get_authorization_url()
    GW->>GW: store CSRF state in Redis
    GW-->>User: 302 redirect → IdP authorize URL
    User->>IDP: login + consent
    IDP-->>CB: GET /auth/callback?code=xxx&state=yyy
    CB->>CB: OAuthService.exchange_code()
    CB->>IDP: POST token_url (code exchange)
    IDP-->>CB: access_token
    CB->>IDP: GET userinfo_url
    IDP-->>CB: {sub, email, name}
    CB->>DB: get_or_create UserNode
    CB->>JWT: issue_access_token(user_id, role)
    CB->>JWT: issue_refresh_token(user_id)
    CB-->>User: {access_token, refresh_token}
```

---

## 4. Agent Skill Execution Flow

```mermaid
sequenceDiagram
    participant API as /app/v1/ route
    participant SREG as SkillRegistryService
    participant WORKER as SkillWorker
    participant MCP as MCPClient
    participant GATE as GatedApprovalService
    participant LLM as LLMClient
    participant DB as AgeGraphStore

    API->>SREG: get(skill_id)
    SREG-->>API: Skill{system_prompt, tools, model}
    API->>WORKER: execute(skill_id, task_id, context)
    WORKER->>DB: get_node(task_id) → TaskNode
    WORKER->>LLM: complete(messages=[system_prompt, task_context])
    LLM-->>WORKER: response with tool_use blocks
    loop For each tool_call
        WORKER->>MCP: call_tool(server_id, tool_name, args)
        MCP->>MCP: check trust_tier
        alt trust_tier == AUTO
            MCP-->>WORKER: tool_result
        else trust_tier == GATED
            MCP->>GATE: request_approval(user_id, ...)
            GATE-->>MCP: approval_id
            Note over GATE: Wait for human approval via /app/v1/mcp-approvals
            GATE-->>MCP: approved / rejected
            MCP-->>WORKER: tool_result or rejection
        end
    end
    WORKER->>DB: update_node(task_id, {state, progress, ...})
    WORKER-->>API: SkillResult{output, tool_calls_made}
```

---

## 5. Task Scoring Pipeline

```mermaid
sequenceDiagram
    participant TRIGGER as Score Trigger<br/>(API call or scheduled)
    participant ENGINE as ScoringEngine
    participant DB as AgeGraphStore
    participant W1 as TimelineUrgency
    participant W2 as DependencyWeight
    participant W3 as CriticalPath
    participant W4 as Blocker
    participant W5 as HumanOverride
    participant W6 as ResourceRisk
    participant W7 as ConstraintPressure

    TRIGGER->>ENGINE: score_task(task_id)
    ENGINE->>DB: get_node(task_id) → TaskNode
    ENGINE->>DB: get_edges(task_id, "out", DEPENDS_ON) → deps
    ENGINE->>DB: get_edges(task_id, "in", BLOCKS) → blockers
    ENGINE->>DB: critical_path(goal_id) → path nodes
    ENGINE->>W1: compute(task.timeline)
    ENGINE->>W2: compute(deps)
    ENGINE->>W3: compute(task.on_critical_path)
    ENGINE->>W4: compute(blockers)
    ENGINE->>W5: compute(task.override)
    ENGINE->>W6: compute(resource.capacity, resource.availability)
    ENGINE->>W7: compute(constraints)
    ENGINE->>ENGINE: computed_priority = Σ(Wi × fi)
    ENGINE->>DB: update_node(task_id, {scoring: ScoringBlock})
    ENGINE-->>TRIGGER: ScoringBlock
```

---

## 6. MCP Tool Call — Connector Sync Flow

```mermaid
sequenceDiagram
    participant SCHED as Trigger Scheduler
    participant CONN as ConnectorRegistry
    participant JIRA as JiraConnector
    participant JIRA_API as Jira REST API
    participant DB as AgeGraphStore
    participant SCORE as ScoringEngine
    participant SSE as SSE Event Stream

    SCHED->>CONN: run_sync(connector_id="jira-prod")
    CONN->>JIRA: sync(workspace_id)
    JIRA->>JIRA_API: GET /rest/api/3/issue?jql=updated>last_sync
    JIRA_API-->>JIRA: list of updated issues
    loop For each issue
        JIRA->>JIRA: map issue → TaskNode properties
        JIRA->>DB: get_node(task_id) or create_node(TaskNode)
        JIRA->>DB: append UpdateLogEntry
        JIRA->>SCORE: score_task(task_id)
        SCORE-->>JIRA: ScoringBlock
        JIRA->>DB: update_node(task_id, {scoring})
    end
    JIRA->>SSE: emit ConnectorSyncCompleteEvent
    JIRA-->>CONN: SyncResult{updated, created, errors}
```

---

## 7. Sub-Agent Parallel Orchestration Flow

*(Main AgentLoop → SubAgentPool → SubAgentRunners → AGENT_UPDATES → Orchestrator re-engagement)*

```mermaid
sequenceDiagram
    actor User
    participant LOOP as AgentLoop<br/>(Orchestrator)
    participant PLANNER as AgentDispatchPlanner
    participant BROKER as Redis Broker
    participant POOL as SubAgentPool<br/>+ BatchCoordinator
    participant RUNNER as SubAgentRunner(s)<br/>(parallel)
    participant LLM as LLMClient
    participant DB as AgeGraphStore
    participant HEALTH as AgentHealthMonitor
    participant CONSUMER as AgentEventConsumer

    User->>LOOP: chat message / trigger
    LOOP->>LLM: process_chat_message() — tool-use loop
    LLM-->>LOOP: delegate_to_agent(task_A), delegate_to_agent(task_B), delegate_to_agent(task_C)

    LOOP->>PLANNER: plan([task_A, task_B, task_C])
    PLANNER->>DB: query DEPENDS_ON edges for {A,B,C}
    DB-->>PLANNER: A depends_on C; B is independent
    PLANNER-->>LOOP: [[task_B, task_C], [task_A]]

    Note over LOOP: Tier 1: B + C in parallel
    LOOP->>BROKER: publish(AGENT_JOBS, job_B {batch_id=tier1})
    LOOP->>BROKER: publish(AGENT_JOBS, job_C {batch_id=tier1})

    POOL->>BROKER: consume AGENT_JOBS
    POOL->>RUNNER: execute(job_B) [background]
    POOL->>RUNNER: execute(job_C) [background]

    par Runner B
        RUNNER->>BROKER: publish(AGENT_UPDATES, AgentTaskStartedEvent B)
        RUNNER->>LLM: LLM loop with invoke_skill / call_mcp_tool
        RUNNER->>BROKER: publish(AGENT_UPDATES, AgentHeartbeatEvent B) every 60s
        RUNNER->>BROKER: publish(AGENT_UPDATES, AgentTaskCompletedEvent B)
    and Runner C
        RUNNER->>BROKER: publish(AGENT_UPDATES, AgentTaskStartedEvent C)
        RUNNER->>LLM: LLM loop with invoke_skill / call_mcp_tool
        RUNNER->>BROKER: publish(AGENT_UPDATES, AgentTaskCompletedEvent C)
    end

    CONSUMER->>BROKER: consume AGENT_UPDATES
    CONSUMER->>DB: update task_B state, task.intelligence
    CONSUMER->>DB: update task_C state, task.intelligence

    POOL->>POOL: BatchCoordinator: tier1 complete (2/2)
    Note over POOL: Dispatch Tier 2: task_A
    POOL->>BROKER: publish(AGENT_JOBS, job_A {batch_id=tier2})
    RUNNER->>BROKER: publish(AGENT_UPDATES, AgentTaskCompletedEvent A)

    POOL->>POOL: BatchCoordinator: all tiers done
    POOL->>BROKER: publish(TRIGGER_EVENTS, DELEGATION_COMPLETE {session_id, results_summary})

    CONSUMER->>LOOP: AgentLoop.process_chat_message(synthetic: "Delegation complete. Results: ...")
    LOOP->>LLM: Synthesize results → decide next actions
    LLM-->>User: Final orchestrated response

    HEALTH->>HEALTH: check_timeouts() every 30s
    alt heartbeat stale > 300s
        HEALTH->>DB: transition(task_id, BLOCKED)
        HEALTH->>BROKER: publish(AGENT_UPDATES, AgentTaskBlockedEvent)
    end
```

---

## 8. API Request with Role-Based Auth (UML Activity)

```mermaid
flowchart TD
    REQ["Incoming HTTP Request"] --> CORS["CORS Middleware"]
    CORS --> RATE["Rate Limit Middleware\n(sliding window per IP)"]
    RATE -->|over limit| R429["HTTP 429\nToo Many Requests"]
    RATE -->|ok| JWT_MW["JWTRoleMiddleware\ndecode Bearer token\nset request.state.user_role"]
    JWT_MW -->|no token| SET_USER["user_role = USER\n(unauthenticated)"]
    JWT_MW -->|valid token| SET_ROLE["user_role = token.role\n(USER / ADMIN)"]
    SET_USER --> ROUTE["Route Handler"]
    SET_ROLE --> ROUTE

    ROUTE --> AUTH_CHECK{{"requires_auth?"}}
    AUTH_CHECK -->|yes| VERIFY["get_current_user_id()\nverify token not revoked"]
    VERIFY -->|invalid| R401["HTTP 401 Unauthorized"]
    VERIFY -->|valid| ADMIN_CHECK{{"require_admin?"}}
    AUTH_CHECK -->|no| HANDLER["Execute Handler Logic"]
    ADMIN_CHECK -->|user_role != ADMIN| R403["HTTP 403 Forbidden"]
    ADMIN_CHECK -->|ADMIN| HANDLER
    HANDLER --> DB_OP["DB / Cache / Storage ops"]
    DB_OP --> RESP["HTTP 200 + JSON body"]
```

---

## 8. Refresh Token Rotation

```mermaid
sequenceDiagram
    participant Client
    participant GW as /auth/refresh
    participant JWT as JWTService
    participant REDIS as Redis<br/>(revocation store)

    Client->>GW: POST {refresh_token: "eyJ..."}
    GW->>JWT: verify_token_async(refresh_token)
    JWT->>REDIS: SISMEMBER revoked_tokens {jti}
    REDIS-->>JWT: not revoked
    JWT-->>GW: payload {sub, type: "refresh", jti}
    GW->>JWT: revoke_token(refresh_token)
    JWT->>REDIS: SADD revoked_tokens {jti} EX token_ttl
    GW->>JWT: issue_access_token(user_id)
    GW->>JWT: issue_refresh_token(user_id)
    GW-->>Client: {access_token, refresh_token, expires_in: 900}
    Note over REDIS: Old refresh token now invalid<br/>Prevents reuse (token rotation)
```
