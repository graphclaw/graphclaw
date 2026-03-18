# GraphClaw Build Agents

Agent definitions used during the Phase 0 multi-agent build.
These document the workstream decomposition, agent roles, and skill assignments
used to build GraphClaw's core loop proof.

## Multi-Agent Architecture

```
┌──────────────────────────────────────────────────────┐
│                  Opus (Architect)                     │
│  Planning · Architecture · Code Review · Decisions   │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐       │
│  │  Sonnet    │ │  Sonnet    │ │  Sonnet    │       │
│  │  WS-A: DB  │ │  WS-B:     │ │  WS-C:     │       │
│  │  Layer     │ │  Models    │ │  Docker    │  ←── Parallel
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘       │
│        │              │              │               │
│  ┌─────▼──────────────▼──────┐                       │
│  │  Sonnet WS-D:             │                       │
│  │  Scoring Engine +         │  ←── Sequential       │
│  │  State Machine            │      (depends A+B)    │
│  └─────────────┬─────────────┘                       │
│  ┌─────────────▼─────────────┐                       │
│  │  Sonnet WS-E:             │                       │
│  │  CLI + Agent Loop         │  ←── Sequential       │
│  │                           │      (depends all)    │
│  └───────────────────────────┘                       │
└──────────────────────────────────────────────────────┘
```

## Agent Files

- `ws-a-database.md` — Database layer workstream
- `ws-b-models.md` — Domain models workstream
- `ws-c-docker.md` — Docker infrastructure workstream
- `ws-d-scoring-state.md` — Scoring engine + state machine workstream
- `ws-e-cli-agent.md` — CLI + agent reasoning loop workstream
- `opus-reviewer.md` — Architecture review checkpoint agent
