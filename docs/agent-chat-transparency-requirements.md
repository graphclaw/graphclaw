# GraphClaw Chat Transparency Requirements and Design

Date: 2026-04-19
Owner: Platform Agent Runtime (GraphClaw backend + Cockpit)
Status: Draft for approval before implementation

## 1. Objective

Provide runtime transparency for the main orchestrator chat session, similar to an interactive coding assistant workflow, across both invocation paths:

1. CLI chat mode
2. Cockpit chat UI

The user should be able to see in real time:

1. What the orchestrator is doing now
2. The plan it is proposing and updating
3. Which tools, skills, and delegated agents are being invoked
4. Status and result summaries for each action
5. Final assistant response as incremental deltas

## 2. Current State Verification

### 2.1 Backend orchestrator

1. `AgentLoop.process_chat_message()` is synchronous for callers and returns a final string after tool rounds.
2. The loop currently calls `llm.complete(...)`, not `llm.stream(...)`.
3. Tool execution is internal to the loop; live tool progress is not emitted to chat consumers.

### 2.2 API layer

1. `POST /app/v1/chat/messages` is request/response and waits for final assistant output.
2. There is no streaming chat endpoint for text deltas and runtime trace events.

### 2.3 Cockpit

1. Chat uses REST mutation and polling (`5s`) for message refresh.
2. Chat UI currently shows a typing indicator only while pending.
3. Existing SSE utility is not wired to application startup.
4. Existing SSE utility uses `onmessage`; backend uses named SSE events (`event: ...`).
5. Cockpit auth relies on Bearer token headers; native browser EventSource cannot send custom Authorization headers.

### 2.4 CLI

1. CLI chat shows a static "thinking" status and prints final response only.
2. No live plan/tool/skill/delegation timeline output exists.

### 2.5 Event plumbing

1. `api/events.py` defines a Redis pubsub-consumed SSE stream model.
2. Reverification did not find an active publisher path to `graphclaw:events:{user_id}` in the current flow.
3. This is a baseline real-time gap independent of chat transparency.

## 3. Scope

### 3.1 In scope

1. Streaming assistant output for orchestrator chat runs.
2. Structured run-trace event stream (planning, tools, skills, delegation).
3. Additive backend API endpoint for streaming chat.
4. Cockpit timeline UI for run transparency.
5. CLI live trace rendering mode.
6. Backward compatibility for existing non-streaming chat endpoint.

### 3.2 Out of scope

1. Emitting hidden raw chain-of-thought.
2. Deep redesign of sub-agent orchestration semantics.
3. Replacing trigger engine architecture.
4. Full event-sourcing replay platform beyond run-level history.

## 4. Product and Safety Requirements

### 4.1 Functional requirements

1. Every chat run emits lifecycle events: started, in-progress, completed/failed.
2. Plan lifecycle events are visible: proposed, revised, awaiting approval.
3. Tool lifecycle events are visible: started, completed/failed, duration.
4. Skill/delegation lifecycle events are visible with status updates.
5. Assistant text is streamed incrementally to CLI and cockpit.

### 4.2 Data safety requirements

1. Do not expose hidden model chain-of-thought.
2. Emit concise rationale summaries only.
3. Event payloads must be allowlisted and sanitized.
4. Never emit secrets, tokens, raw credentials, or unsafe MCP payloads.
5. Audit logs and UI trace stream remain separate concerns.

### 4.3 Compatibility requirements

1. Existing `POST /app/v1/chat/messages` must continue unchanged.
2. Streaming path must be additive (`/stream` endpoint).
3. CLI and cockpit can adopt streaming independently.

## 5. High-Level Design

### 5.1 Run event model

Introduce a run-scoped event contract with correlation fields:

1. `run_id`
2. `session_id`
3. `user_id`
4. `event_seq` (monotonic per run)
5. `timestamp`
6. `event_type`
7. `payload` (typed, allowlisted)

### 5.2 Event taxonomy

1. Run lifecycle
   - `run.started`
   - `run.completed`
   - `run.failed`

2. Planning
   - `plan.started`
   - `plan.proposed`
   - `plan.revised`
   - `plan.awaiting_approval`

3. Assistant output
   - `assistant.delta`
   - `assistant.final`

4. Tooling
   - `tool.started`
   - `tool.completed`
   - `tool.failed`

5. Skills
   - `skill.started`
   - `skill.progress`
   - `skill.completed`
   - `skill.failed`

6. Delegation
   - `delegate.started`
   - `delegate.progress`
   - `delegate.completed`
   - `delegate.blocked`

7. MCP
   - `mcp.started`
   - `mcp.completed`
   - `mcp.failed`

### 5.3 Transport design

Chat streaming transport:

1. Use authenticated fetch-stream over `POST` returning `text/event-stream`.
2. Reason: request body + Authorization header are required.
3. Native EventSource is not suitable for this chat path due to header limitation.

Global non-chat event stream:

1. Existing `GET /app/v1/events` can continue to use EventSource semantics.
2. Cockpit listener logic must support named events via `addEventListener`.

### 5.4 API design

Add endpoint:

1. `POST /app/v1/chat/messages/stream`

Behavior:

1. Accept same input payload as existing chat POST (`content`).
2. Stream run events and assistant deltas as SSE-framed data in response body.
3. Emit terminal event (`run.completed` or `run.failed`) for every run.
4. Persist final user/assistant chat messages once run completes.

Keep endpoint:

1. `POST /app/v1/chat/messages` unchanged for compatibility.

### 5.5 Orchestrator execution design

Add stream-capable method in loop:

1. `process_chat_message_stream(...) -> AsyncIterator[AgentRunEvent]`

Execution shape:

1. Emit `run.started`.
2. Build system context and emit context summary event.
3. Stream assistant deltas using provider `stream()` where possible.
4. On tool request, emit `tool.started` before execution and terminal status after.
5. Bridge skill/delegation progress to run events.
6. Emit `assistant.final` and then terminal run event.

### 5.6 Publisher design

Introduce a user-event publisher abstraction independent of queue broker list semantics:

1. `UserEventPublisher` interface
2. Redis pubsub implementation for `graphclaw:events:{user_id}`
3. Optional no-op/in-memory fallback for dev/test

## 6. Files to Modify or Create

### 6.1 Backend files to modify

1. `src/graphclaw/agent/loop.py`
   - Add stream method and event emission
   - Keep current method as wrapper/fallback

2. `src/graphclaw/api/chat.py`
   - Add streaming route and response framing
   - Persist final history entries on terminal event

3. `src/graphclaw/gateway/app.py`
   - Wire user-event publisher into app state
   - Inject publisher into AgentLoop and related services

4. `src/graphclaw/api/events.py`
   - Align documented/handled event types with new taxonomy

5. `src/graphclaw/agent/event_consumer.py`
   - Forward sub-agent updates as delegation trace events (sanitized)

6. `src/graphclaw/cli/agent_commands.py`
   - Add live trace rendering mode

### 6.2 Backend files to create

1. `src/graphclaw/agent/run_events.py`
   - Pydantic event schema, versioning, payload allowlists

2. `src/graphclaw/infra/user_events.py`
   - Publisher abstraction + Redis implementation

3. `src/graphclaw/api/chat_streaming.py` (optional split)
   - Stream helpers and SSE framing utilities

### 6.3 Cockpit files to modify

1. `src/lib/api-hooks.ts`
   - Add streaming chat hook using fetch-stream parser

2. `src/features/chat/ChatView.tsx`
   - Render assistant deltas live
   - Render execution timeline panel

3. `src/lib/sse.ts`
   - Add named-event listener support for global events

4. `src/app.tsx`
   - Wire global SSE lifecycle if required by app-level updates

### 6.4 Cockpit files to create

1. `src/lib/chat-stream.ts`
   - Parse SSE frames from fetch response body
   - Emit typed callbacks for run events

### 6.5 Tests to add/modify

1. Backend API tests: stream event ordering, terminal event guarantee, persistence semantics
2. Loop tests: tool and run lifecycle emission, payload sanitization
3. Cockpit tests: delta rendering and timeline state progression
4. CLI tests: live trace output mode

## 7. Logic to Implement

1. Correlation and ordering
   - Monotonic `event_seq` per run
   - Required terminal event for all success/failure exits

2. Tool instrumentation
   - Emit `tool.started` before `_execute_tool`
   - Emit `tool.completed` with latency and safe summary
   - Emit `tool.failed` with safe error metadata

3. LLM streaming
   - Prefer provider `stream()` for deltas
   - Maintain round-based tool loop compatibility

4. Delegation and skills
   - Map AGENT_UPDATES/sub-agent status to run-level delegation events
   - Surface progress without exposing sensitive internal content

5. Persistence
   - Write user message at run start
   - Write final assistant message on terminal event

## 8. Reverification Gap Findings

1. Gap A: No verified active publisher path to documented SSE Redis channel.
2. Gap B: Cockpit SSE listener mismatch (`onmessage` only) for named event frames.
3. Gap C: Cockpit chat is polling-based, not stream-capable.
4. Gap D: CLI has no live trace rendering.
5. Gap E: No unified run-event schema or versioning exists.
6. Gap F: Provider stream behavior differs for tool metadata; loop instrumentation is mandatory.
7. Gap G: EventSource auth mismatch with current Bearer-header model in cockpit.
8. Gap H: Duplicate concerns between audit logs and UX trace stream require strict boundary.

## 9. Design Decisions Required (Approval Gate)

1. Approve additive endpoint: `POST /app/v1/chat/messages/stream`.
2. Approve fetch-stream transport for chat transparency in cockpit.
3. Approve event taxonomy and schema versioning approach.
4. Approve sanitization policy (summary-only rationale, no hidden chain-of-thought).
5. Approve phased implementation order in Section 10.

## 10. Phased Implementation Plan

1. Phase T1: Event schema + publisher foundation
2. Phase T2: Backend streaming chat route + loop instrumentation
3. Phase T3: Cockpit streaming + timeline UI
4. Phase T4: CLI live trace mode
5. Phase T5: Test hardening and reconnect behavior

## 11. Acceptance Criteria

1. Cockpit chat shows incremental assistant output for active run.
2. Cockpit shows live run timeline for plan/tool/skill/delegation events.
3. CLI can render live trace events in-order.
4. Existing non-streaming chat endpoint remains functional.
5. Stream payloads pass sanitization policy checks.
6. Tests cover success and failure terminal event paths.

## 12. Progress Log

| Timestamp (UTC) | Step | Status | Notes |
|---|---|---|---|
| 2026-04-19T00:00:00Z | Created requirements/design reference document | DONE | Added objective, scope, architecture, file-level plan |
| 2026-04-19T00:00:00Z | Verified current backend, API, cockpit, and CLI behavior | DONE | Confirmed non-streaming chat path and missing run-trace visibility |
| 2026-04-19T00:00:00Z | Reverified event plumbing and identified architecture gaps | DONE | Confirmed missing publisher path and listener mismatch |
| 2026-04-19T00:00:00Z | Added implementation matrix: files to modify/create and logic scope | DONE | Included backend, cockpit, CLI, and test surfaces |
| 2026-04-19T00:00:00Z | Reverified transport/auth fit and adjusted design | DONE | Switched chat transport recommendation to authenticated fetch-stream |
| 2026-04-19T00:00:00Z | Implementation execution | BLOCKED (awaiting approval) | Per request, no code implementation until design approval |

## 13. Review Checklist

1. Confirm event names and required payload fields.
2. Confirm chat stream endpoint path and response framing.
3. Confirm cockpit UX expectations for timeline granularity.
4. Confirm CLI trace-mode output format.
5. Confirm sanitization/redaction policy before coding.

After approval, implementation will proceed phase-by-phase and this document will be updated at each milestone.
