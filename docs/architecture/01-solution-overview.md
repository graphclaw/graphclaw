# 01 — Solution Overview

GraphClaw is an **AI-native task orchestration platform**. It turns natural-language
communication from any channel (email, Slack, Teams …) into a live property graph of
tasks, goals, resources, and constraints — then uses AI agents to prioritise, delegate,
and track work autonomously.

---

## Six Capability Layers

```mermaid
block-beta
  columns 1

  block:channels["① Channel Gateway"]
    email["Email"] slack["Slack"] teams["Teams"] telegram["Telegram"] whatsapp["WhatsApp"]
  end

  block:auth["② Auth & Security"]
    oauth["OAuth 2.0\n(Google/GitHub/MS)"] jwt["RS256 JWT"] rbac["RBAC Middleware"] rate["Rate Limiter"]
  end

  block:api["③ Application API  (/app/v1/*)   — 131+ routes"]
    graph["Graph\n(tasks/goals)"] scoring["Scoring\nEngine"] state["State\nMachine"] chat["Chat"] admin["Admin\nPanel"]
  end

  block:agent["④ Agent Layer"]
    llm["LLM Clients\n(Claude/GPT/LiteLLM)"] mcp["MCP Tool\nCalls"] skills["Skill\nWorkers"] delegation["Delegation\n& Escalation"]
  end

  block:data["⑤ Data Layer"]
    age["PostgreSQL+AGE\n(property graph)"] redis["Redis\n(cache/broker)"] s3["MinIO / S3\n(objects)"] secrets["Secrets\n(env/AWS SM)"]
  end

  block:connectors["⑥ External Connectors"]
    jira["Jira"] notion["Notion"] asana["Asana"] gcal["Google Cal"] outlook["Outlook Cal"]
  end

  channels --> auth
  auth --> api
  api --> agent
  agent --> data
  data --> connectors
```

---

## High-Level Architecture Block Diagram

```mermaid
flowchart TB
    subgraph EXTERNAL["External World"]
        direction LR
        U["👤 User / Browser"]
        CH["📨 Channels\n(Email·Slack·Teams\nTelegram·WhatsApp)"]
        EXT["🌐 External Services\n(Jira·Notion·Asana\nGitHub·Google Cal)"]
        AI_API["🤖 LLM APIs\n(Anthropic·OpenAI\nLiteLLM)"]
    end

    subgraph GW["GraphClaw Gateway  :8000"]
        direction TB
        MW["Middleware Stack\nCORS · Rate Limit · JWT Role"]
        AUTH["/auth/*\nOAuth 2.0 + JWT"]
        INBOUND["/api/v1/inbound\nMessage ingestion"]
        OUTBOUND["/api/v1/outbound\nMessage delivery"]
        A2A["/a2a/*\nAgent-to-Agent protocol"]
        APP["/app/v1/*\n131+ Application routes"]
    end

    subgraph CHANNELS["Channel Adapters (in-process)"]
        direction LR
        EADP["EmailAdapter\nEmailPoller\nEmailSender"]
        SADP["SlackAdapter\nSlackSender"]
        TADP["TeamsAdapter\nTeamsSender"]
        XADP["Telegram·WhatsApp\nAdapters+Senders"]
    end

    subgraph AGENT["Agent Layer (in-process)"]
        direction LR
        SKILL["SkillWorker\nSkillRegistryService"]
        MCP_C["MCPClient\nMCPRegistry\nGatedApprovalService"]
        LLM["LLMClient\n(Anthropic/OpenAI/LiteLLM)"]
        DELEG["DelegationService\nEscalationService"]
    end

    subgraph DATA["Data Layer"]
        PG[("PostgreSQL 18\n+ Apache AGE\n+ pgvector")]
        RD[("Redis 7\ncache + broker")]
        S3_[("MinIO / S3\nobject store")]
        SEC["SecretsClient\n(env / AWS SM / Vault)"]
    end

    subgraph CONN["Connectors (in-process)"]
        direction LR
        JIRAC["Jira"]
        NOTC["Notion"]
        ASAC["Asana"]
        CALC["Google Cal\nOutlook Cal"]
    end

    U -->|HTTPS| GW
    CH -->|webhook / IMAP poll| CHANNELS
    CHANNELS -->|publish to Redis| RD
    RD -->|consume| AGENT

    GW --> MW
    MW --> AUTH
    MW --> INBOUND
    MW --> OUTBOUND
    MW --> A2A
    MW --> APP

    APP --> DATA
    AGENT --> LLM --> AI_API
    AGENT --> MCP_C --> EXT
    CONN --> EXT
    APP --> CONN

    DATA --> PG
    DATA --> RD
    DATA --> S3_
    DATA --> SEC
```

---

## Request Lifecycle (Happy Path)

```mermaid
sequenceDiagram
    actor User
    participant GW as Gateway :8000
    participant MW as Middleware
    participant API as App API (/app/v1)
    participant DB as PostgreSQL+AGE
    participant Cache as Redis

    User->>GW: HTTPS request + Bearer token
    GW->>MW: CORS check
    MW->>MW: Rate limit check
    MW->>MW: JWTRoleMiddleware — decode token → set user_role
    MW->>API: Forward request + user_id + user_role
    API->>Cache: Read cached data (if applicable)
    Cache-->>API: Hit / Miss
    API->>DB: Cypher / SQL query
    DB-->>API: Result rows (agtype)
    API->>Cache: Write-through update
    API-->>GW: JSON response
    GW-->>User: HTTP 200 + body
```

---

## Extensibility Philosophy

GraphClaw is designed so every integration point is an **abstract base class** backed by a pluggable implementation:

| Extension Point | ABC | Current Implementations | Add New By |
|----------------|-----|------------------------|------------|
| Graph database | `GraphStore` | `AgeGraphStore` | Implement ABC → wire in `db/factory.py` |
| Query engine | `GraphQueryEngine` | `AgeGraphQueryEngine` | Same pattern |
| Object storage | `StorageClient` | `S3StorageClient` | Implement ABC → inject in gateway lifespan |
| Secrets | `SecretsClient` | `EnvFile`, `AWSSecrets`, `HashiCorpVault` | Implement ABC |
| LLM | `LLMClient` | `AnthropicLLMClient`, `OpenAILLMClient`, `LiteLLMLLMClient` | New module in `llm/` |
| Channel | `ChannelAdapter` | Email, Slack, Teams, Telegram, WhatsApp | New folder in `gateway/channels/` |
| Connector | `ConnectorBase` | Jira, Notion, Asana, Google Cal, Outlook | New folder in `connectors/` |
| MCP adapter | MCP protocol | GitHub, Google Cal, Slack | New folder in `mcp/adapters/` |
