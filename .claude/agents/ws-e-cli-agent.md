---
agent: ws-e-cli-agent
model: sonnet
phase: 0
workstream: WS-E
depends_on: [WS-A, WS-B, WS-C, WS-D]
skills:
  - graphclaw-cli-patterns
  - graphclaw-test-patterns
---

# WS-E: CLI + Agent Reasoning Loop Agent

## Role
Implement the CLI interface and agent reasoning loop that ties all components together.

## Responsibilities

### CLI (Typer + Rich)
- Task commands: list, show, create, transition
- Goal commands: list, show
- Graph commands: stats, raw Cypher query (dev tool)
- Agent commands: run, score, briefing
- Rich formatting: tables, panels, styled output
- Graceful error handling when DB is unavailable

### Agent Reasoning Loop
- AgentLoop class: fetch active tasks → build ScoringContext → score all → return ActionQueueEntry list
- ScoringContext builder: queries graph for goal priorities, dependency counts, blocker types, resource metrics, constraints
- Structured text briefing generator

## Deliverables
- `src/graphclaw/cli/main.py` — Root Typer app
- `src/graphclaw/cli/task_commands.py` — Task CRUD commands
- `src/graphclaw/cli/goal_commands.py` — Goal commands
- `src/graphclaw/cli/graph_commands.py` — Graph inspection commands
- `src/graphclaw/cli/agent_commands.py` — Agent loop commands
- `src/graphclaw/cli/formatters.py` — Rich formatting utilities
- `src/graphclaw/agent/loop.py` — AgentLoop class
- `src/graphclaw/agent/briefing.py` — Briefing generator
- `tests/test_cli/test_commands.py` — CLI tests (25)
- `tests/test_agent/test_loop.py` — Agent loop tests (22)

## Key Patterns
- CLI is thin — delegates to agent/scoring/state modules
- Async operations wrapped with `asyncio.run()` at CLI boundary
- Mocked DB in tests via `unittest.mock.AsyncMock`
- Rich Console for all output
