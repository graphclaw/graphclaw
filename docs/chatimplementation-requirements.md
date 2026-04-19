# GraphClaw Chat Transparency — Implementation Requirements

Date: 2026-04-19
Status: Implementation active

## 1. What we are building

Make every orchestrator chat session fully observable: stream assistant text as it
is generated, and emit structured trace events so the user can see — in real time —
each plan, tool call, skill invocation, delegation step, and its result.

Scope covers three surfaces:
1. Backend FastAPI service — new streaming endpoint + AgentLoop stream method
2. CLI — live trace rendering mode
3. Cockpit UI — incremental assistant text + execution timeline panel

## 2. New files

### 2.1 `src/graphclaw/agent/run_events.py`

Pydantic models for the run-event contract:

- `RunEventType` — `Literal` enum of all event types
- `AgentRunEvent` — base model: `run_id`, `session_id`, `user_id`, `event_seq`, `timestamp`, `event_type`, `schema_version`, `payload`
- Payload models per category (allowlisted fields, no secrets)
  - `RunStartedPayload`
  - `RunCompletedPayload`
  - `RunFailedPayload`
  - `PlanProposedPayload` — `plan_id`, `step_count`, `summary`
  - `AssistantDeltaPayload` — `delta` (text chunk)
  - `AssistantFinalPayload` — `content_length`, `input_tokens`, `output_tokens`
  - `ToolStartedPayload` — `tool_name`, `args_summary` (sanitized, max 200 chars)
  - `ToolCompletedPayload` — `tool_name`, `latency_ms`, `result_summary`
  - `ToolFailedPayload` — `tool_name`, `error_class`, `error_message`
  - `SkillStartedPayload` — `skill_name`, `task_id`
  - `SkillProgressPayload` — `skill_name`, `task_id`, `message`
  - `SkillCompletedPayload` — `skill_name`, `task_id`, `duration_ms`
  - `SkillFailedPayload` — `skill_name`, `task_id`, `reason`
  - `DelegateStartedPayload` — `agent_id`, `task_id`, `batch_id`
  - `DelegateProgressPayload` — `agent_id`, `task_id`, `message`
  - `DelegateCompletedPayload` — `agent_id`, `task_id`, `status`, `duration_ms`
  - `DelegateBlockedPayload` — `agent_id`, `task_id`, `reason`
  - `McpStartedPayload` — `server_id`, `tool_name`
  - `McpCompletedPayload` — `server_id`, `tool_name`, `latency_ms`
  - `McpFailedPayload` — `server_id`, `tool_name`, `error_class`
- Helper `make_event(event_type, run_id, session_id, user_id, seq, payload)` — factory

### 2.2 `src/graphclaw/infra/user_events.py`

Publisher abstraction for per-user UI event delivery:

- `UserEventPublisher` ABC: `async publish(user_id, event)` + `async close()`
- `RedisUserEventPublisher` — publishes JSON to `graphclaw:events:{user_id}` via Redis pubsub
- `InMemoryUserEventPublisher` — used in testing; stores events in a `list`
- `NullUserEventPublisher` — no-op for environments without Redis

### 2.3 `src/graphclaw/api/chat_streaming.py`

Helpers for the streaming endpoint:

- `sse_frame(event_type, data)` — format SSE-framed string
- `stream_chat_run(agent_loop, user_id, text, history, session_id, storage_client)` — async generator
  yielding SSE frames
- Wraps `AgentLoop.process_chat_message_stream` and formats each `AgentRunEvent`
- Persists user/assistant messages to history on terminal event

### 2.4 `src/lib/chat-stream.ts` (Cockpit)

- `ChatStreamParser` — reads `ReadableStream` from fetch response
- `ChatStreamEvent` typed union per event type
- `startChatStream(content, onEvent)` — opens authenticated fetch-SSE stream

## 3. Files to modify

### 3.1 `src/graphclaw/agent/loop.py`

Add method:
```
async def process_chat_message_stream(
    self,
    user_id: str,
    text: str,
    conversation_history: list[dict] | None = None,
    session_id: str | None = None,
    publisher: UserEventPublisher | None = None,
) -> AsyncIterator[AgentRunEvent]
```

Execution contract:
1. Emit `run.started`
2. Build messages and emit `context.ready` if there is graph context
3. Enter agentic loop:
   a. Use `llm.stream(messages)` to yield `assistant.delta` chunks
   b. Collect stream to detect embedded `tool_use` stop signals
   c. For text-only turns emit `assistant.final`
   d. For tool-use turns collect tool calls, then emit `tool.started` + `tool.completed/failed` per call
4. Emit `run.completed` or `run.failed`

Add DI param: `publisher: UserEventPublisher | None = None`

### 3.2 `src/graphclaw/api/chat.py`

Add route:
```
POST /app/v1/chat/messages/stream
```
Returns `StreamingResponse(media_type="text/event-stream")` backed by `stream_chat_run`.

### 3.3 `src/graphclaw/gateway/app.py`

- Instantiate `RedisUserEventPublisher` when Redis is available; `NullUserEventPublisher` otherwise
- Inject into `AgentLoop` constructor
- Add cleanup to lifespan shutdown

### 3.4 `src/graphclaw/api/events.py`

- Add new event types to documented list
- No behavior change

### 3.5 `src/graphclaw/cli/agent_commands.py`

- Add `--trace` flag to `agent chat` command
- When `--trace` is set, consume stream endpoint and print each event live with Rich formatting

### 3.6 `src/lib/api-hooks.ts` (Cockpit)

- Add `useChatStream(text, enabled)` hook that initiates fetch-stream and returns `{events, delta, status}`

### 3.7 `src/features/chat/ChatView.tsx` (Cockpit)

- Add streaming mode toggle
- Render incremental text as `assistant.delta` arrives
- Show execution timeline sidebar with tool/plan/delegation events

## 4. Transport

Chat streaming uses **POST returning `text/event-stream`** body.

Reason: requires `Authorization` header + request body; native EventSource does not support this.

Global events (`/app/v1/events`) remain EventSource-compatible after fixing cockpit listener.

## 5. Event safety rules

- `args_summary`: max 200 chars, strip keys matching `/secret|token|password|key|credential/i`
- `result_summary`: max 300 chars, same key strip
- `error_message`: max 200 chars, strip
- No chain-of-thought text
- No raw tool argument objects

## 6. Test requirements

Integration tests only against real implementations.

### 6.1 `tests/test_agent/test_run_events.py`
- Model construction and serialization
- `event_seq` monotonic ordering
- Terminal event guarantee across success and failure paths
- Payload sanitization

### 6.2 `tests/test_infra/test_user_events.py`
- `RedisUserEventPublisher` round-trip against live Redis
- Event delivery ordering with multiple concurrent publishes

### 6.3 `tests/test_agent/test_loop_stream_integration.py`
- `process_chat_message_stream` emits ordered events against real PostgreSQL+AGE + MinIO
- `run.started` and terminal `run.completed/failed` present
- `tool.started` emitted for every tool call
- `assistant.delta` emits multiple chunks
- History persisted after terminal event

### 6.4 `tests/test_api/test_chat_stream.py`
- FastAPI TestClient against streaming route
- Correct `text/event-stream` content type
- Full event sequence parseable from response body

## 7. Acceptance criteria

1. Stream endpoint returns SSE frames
2. Every run has exactly one terminal event
3. `event_seq` is monotonic and starts at 0 for each run
4. History is persisted after run completes
5. All integration tests pass against real MinIO and PostgreSQL+AGE
6. Existing `POST /app/v1/chat/messages` passes unchanged

## 8. Progress

| Step | Status | Notes |
|---|---|---|
| Requirements doc written | DONE | This file |
| `run_events.py` | IN PROGRESS | |
| `user_events.py` | PENDING | |
| `loop.py` stream method | PENDING | |
| `chat.py` stream route | PENDING | |
| `chat_streaming.py` | PENDING | |
| `gateway/app.py` wiring | PENDING | |
| CLI trace mode | PENDING | |
| Cockpit chat-stream.ts | PENDING | |
| Cockpit ChatView stream | PENDING | |
| Integration tests | PENDING | |
| Tests passing | PENDING | |
| Git commit | PENDING | |
