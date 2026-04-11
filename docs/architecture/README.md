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
