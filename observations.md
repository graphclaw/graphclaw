# GraphClaw - Observations (Proposed Changes)

This file tracks proposed implementation work from the 2026-04-23 code review discussion.
It intentionally excludes already completed historical observations captured in observations-done.md.

## Index
1. Design Review Set A (First 5)
2. Security and Access Control
3. Reliability and Runtime Safety
4. Functional Gaps vs Product Design
5. Code Quality and Maintainability
6. Validation and Release Gates

## 1) Design Review Set A (First 5)

- [x] N-001 | Priority: P0 | Status: Completed (Committed)
  Add ownership authorization checks to state transition endpoints so users can only transition tasks they own or are permitted to modify.
  Design details:
  - API path: src/graphclaw/api/state.py transition_task().
  - Authorization rule: allow transition when requester is owner (owned_by), assignee (assigned_to), or owner via OWNED_BY edge.
  - Security behavior: return HTTP 403 when authorization fails; preserve existing 404/422 behavior.
  - Compatibility: retain support for legacy rows where owned_by may be absent by checking OWNED_BY graph edge as fallback.
  - Test scope: add integration coverage for allowed owner/assignee and denied non-owner paths.
  Completion evidence:
  - Implementation: src/graphclaw/api/state.py (_is_transition_authorized + 403 enforcement in transition_task).
  - Tests: tests/test_state/test_state_history_integration.py::test_assignee_can_transition_task and ::test_unrelated_user_gets_403_on_transition.
  - Commit: b16505f.

- [x] N-002 | Priority: P0 | Status: Completed (Committed)
  Wire PART_OF edge creation when create_task receives parent_goal_id so goal-task hierarchy is represented in graph queries and UI.
  Design details:
  - API path: src/graphclaw/api/graph.py create_task().
  - Behavior: when parent_goal_id is provided, create PART_OF edge task_id -> parent_goal_id after node creation.
  - Validation: confirm parent_goal_id exists before edge wiring and return HTTP 422 for invalid references.
  - Consistency: keep existing OWNED_BY and ASSIGNED_TO edge creation behavior unchanged.
  - Test scope: add integration test that verifies PART_OF edge exists for parent_goal_id and absent otherwise.
  Completion evidence:
  - Implementation: src/graphclaw/api/graph.py (parent_goal_id existence validation + PART_OF edge creation/rollback path).
  - Tests: tests/test_graph/test_edge_wiring_integration.py::test_part_of_edge_created_when_parent_goal_id_provided and ::test_parent_goal_id_requires_existing_node.
  - Commit: b16505f.

- [x] N-003 | Priority: P0 | Status: Completed (Committed)
  Add wall-clock timeout controls to sub-agent execution loop and per-tool invocations (skill and MCP calls) to prevent stuck runners.
  Design details:
  - Runtime path: src/graphclaw/agent/sub_agent_runner.py.
  - Add max runner execution timeout around _run_llm_loop() using asyncio.timeout.
  - Add per-call tool timeouts for worker.execute() and MCP client call_tool().
  - Outcome mapping: timeout emits BLOCKED update event and final status TIMED_OUT for downstream health handling.
  - Config wiring: extend AgentPoolConfig + gateway startup to pass timeout values from env.
  - Test scope: add unit tests for runner timeout and tool timeout failure payloads.
  Completion evidence:
  - Implementation:
    - src/graphclaw/agent/sub_agent_runner.py (execution timeout + per-tool timeout wrappers).
    - src/graphclaw/agent/sub_agent_pool.py (timeout plumbing to runners).
    - src/graphclaw/infra/config.py + src/graphclaw/gateway/app.py (env/config wiring).
  - Tests: tests/test_agent/test_sub_agent_orchestration.py::TestSubAgentRunnerTimeouts.
  - Commit: b16505f.

- [x] N-004 | Priority: P0 | Status: Completed (Committed)
  Implement follow-up timing formula using base cadence, complexity factor, resource reliability, and recency adjustment instead of fixed 48h defaults.
  Design details:
  - Formula source: src/graphclaw/triggers/followup.py compute_next_followup().
  - Replace fixed +48h scheduling in delegated task creation paths:
    - src/graphclaw/api/graph.py create_task()
    - src/graphclaw/agent/main_orchestrator.py _tool_create_task()
  - Inputs:
    - base cadence from defaults/user preference block when available
    - complexity factor from delegated-task context
    - reliability from assignee resource node if present, else default reliability
    - recency bonus default to 0 when no history is available
  - Test scope: validate scheduled_fire_at reflects formula and responds to lower reliability with shorter cadence.
  Completion evidence:
  - Implementation:
    - src/graphclaw/api/graph.py (delegated follow-up scheduling now uses compute_next_followup()).
    - src/graphclaw/agent/main_orchestrator.py (_tool_create_task delegated path now formula-based).
  - Tests: tests/test_agent/test_followup_spawn_integration.py::test_delegated_followup_schedule_uses_formula_cadence and ::test_agent_delegated_followup_schedule_uses_formula.
  - Commit: b16505f.

- [x] N-005 | Priority: P0 | Status: Completed (Committed)
  Start and verify scheduled scoring cycles so computed_priority and ranked queues are continuously updated from real graph data.
  Design details:
  - Runtime wiring: start TriggerEngine in gateway lifespan and stop it during shutdown.
  - Scheduler source: load persisted trigger configs and register them with TriggerScheduler at startup.
  - Execution path: TriggerEngine publishes TRIGGER_EVENTS -> AgentEventConsumer handles TIME_BASED/EVENT_BASED -> MainOrchestrator.run_cycle().
  - Observability: log trigger-engine startup and number of loaded schedules.
  - Test scope: integration verification that scheduled/event trigger path executes scoring cycle against real DB data.
  Completion evidence:
  - Implementation: src/graphclaw/gateway/app.py (lifespan now loads persisted trigger configs/fallback schedule, starts TriggerEngine on startup, stops it on shutdown).
  - Validation:
    - Runtime regression: tests/test_gateway/test_app.py.
    - End-to-end targeted suite passed against live local services.
  - Commit: b16505f.

## 2) Security and Access Control

- [x] N-006 | Priority: P0 | Status: Completed (Committed)
  Validate OAuth redirect base URL against an allowlist and strict URI parsing to prevent open redirect risk.
  Design details:
  - Auth path: src/graphclaw/auth/routes.py (_build_redirect_uri).
  - Added strict base URL normalization/validation (scheme, host, no userinfo/query/fragment).
  - Enforced allowlist via OAUTH_REDIRECT_ALLOWLIST (comma-separated base URLs, secure localhost-only default when unset).
  - HTTP policy: non-localhost redirects must use https; invalid config returns HTTP 503 in login/callback.
  - Provider hardening: callback now explicitly rejects unsupported providers with HTTP 400 before token exchange.
  Completion evidence:
  - Implementation: src/graphclaw/auth/routes.py (_normalize_redirect_base_url, _load_redirect_allowlist, login/callback enforcement).
  - Tests: tests/test_auth/test_oauth_redirect_validation.py.
  - Commit: 5ca77c4.

- [x] N-007 | Priority: P0 | Status: Completed (Committed)
  Harden Cypher query construction by auditing all dynamic query paths and enforcing strict escaping and allowlist rules.
  Design details:
  - Repository path: src/graphclaw/db/age/repository.py list_nodes().
  - Added label allowlist validation for dynamic MATCH label interpolation (same identifier regex family already used for filter keys).
  - Preserved existing strict filter-key validation and literal escaping path for values.
  - Security behavior: reject malformed/injection-shaped labels and keys with ValueError before query execution.
  Completion evidence:
  - Implementation: src/graphclaw/db/age/repository.py (label validation guard in list_nodes).
  - Tests: tests/test_db/test_graph_repository.py::TestListNodes::test_list_nodes_rejects_invalid_label and ::test_list_nodes_rejects_invalid_filter_key.
  - Commit: 5ca77c4.

- [x] N-008 | Priority: P0 | Status: Completed (Committed)
  Add prompt-injection hardening in inbound intelligence processing (message boundary controls, constrained extraction schema, sanitization).
  Design details:
  - Runtime path: src/graphclaw/inbound/intelligence_agent.py.
  - Added strict JSON extraction pipeline with bounded payload size, object-type enforcement, and key allowlist (task_entry/memory_note only).
  - Added value normalization (single-line compaction, max field length) and type checks (string/null only).
  - Prompt boundary update: inbound message body is wrapped in explicit <message> tags and treated as untrusted data.
  - Failure mode: malformed/extra-key payloads now map to parse_error path with action_taken="error".
  Completion evidence:
  - Implementation: src/graphclaw/inbound/intelligence_agent.py (_extract_json_object, _parse_extraction_payload, strict parsing in process()).
  - Tests: tests/test_inbound/test_intelligence_agent.py::test_parse_extraction_payload_extracts_json_from_wrapped_output and ::test_process_rejects_payload_with_unexpected_keys.
  - Commit: 5ca77c4.

- [x] N-009 | Priority: P1 | Status: Completed (Committed)
  Add strict input validation for storage path segments (channel, topic, filename, ids) to prevent traversal and malformed key writes.
  Design details:
  - Storage path registry: src/graphclaw/infra/storage.py.
  - Added centralized validators for single segments and relative subpaths to block separators, traversal tokens, and null bytes.
  - Applied validation across dynamic StoragePaths builders (user/agent/skill/session/attachment/log/system paths).
  - Added canonical user-scoped chat path helper (StoragePaths.chat_history) to avoid ad-hoc key construction.
  Completion evidence:
  - Implementation: src/graphclaw/infra/storage.py (new validators + validated path methods), src/graphclaw/api/chat.py (_history_path uses StoragePaths.chat_history).
  - Tests: tests/test_infra/test_storage_paths.py (new validation cases + chat path case), tests/test_api/test_chat_history_integration.py::TestGenerateAgentResponseWithRealStorage::test_history_persisted_after_send.
  - Commit: 5ca77c4.

- [x] N-010 | Priority: P1 | Status: Completed (Committed)
  Ensure chat history and state mutation APIs consistently enforce user scope and ownership checks.
  Design details:
  - Graph task endpoints: src/graphclaw/api/graph.py.
  - Added shared _is_task_authorized() rule for task detail/update/delete: owner (owned_by), assignee (assigned_to), or OWNED_BY edge fallback.
  - Enforced HTTP 403 for unauthorized get/patch/delete task operations.
  - Chat scope alignment: chat history storage now uses per-user prefix via StoragePaths.chat_history.
  Completion evidence:
  - Implementation: src/graphclaw/api/graph.py (authorization helper + 403 enforcement), src/graphclaw/api/chat.py (user-scoped storage path helper).
  - Tests: tests/test_api/test_graph_access_control.py, tests/test_auth/test_provisioning_integration.py::TestCallbackProvisionsUser::test_callback_creates_usernode_via_provisioning.
  - Commit: 5ca77c4.

## 3) Reliability and Runtime Safety

- [ ] N-011 | Priority: P0 | Status: Proposed
  Add execution timeout and cancellation handling in SubAgentRunner loop with explicit timeout events and blocked-state escalation.

- [ ] N-012 | Priority: P1 | Status: Proposed
  Add per-call timeout wrappers for worker.execute and MCP client calls with retry policy where safe and idempotent.

- [ ] N-013 | Priority: P1 | Status: Proposed
  Remove dependence on private semaphore internals in SubAgentPool metrics and track active/waiting counts explicitly.

- [ ] N-014 | Priority: P1 | Status: Proposed
  Fail fast for critical startup dependencies (DB, storage, broker) or expose explicit degraded-mode readiness with alarms.

- [ ] N-015 | Priority: P1 | Status: Proposed
  Add startup/runtime validation to ensure heartbeat interval and timeout values are coherent.

## 4) Functional Gaps vs Product Design

- [ ] N-016 | Priority: P0 | Status: Proposed
  Implement propose-plan review lifecycle: draft plan persistence, human approval/edit stage, then atomic execute-plan commit.

- [ ] N-017 | Priority: P1 | Status: Proposed
  Implement bottom-up goal inference from task clusters and relationship patterns.

- [ ] N-018 | Priority: P1 | Status: Proposed
  Implement HandoffNode schema and related edge wiring where required by design spec.

- [ ] N-019 | Priority: P1 | Status: Proposed
  Evaluate and implement Dependency Gate as a first-class node if required by final design decision.

- [ ] N-020 | Priority: P1 | Status: Proposed
  Connect inbound processor to live channels (email/Slack/etc.) with production-ready ingestion path.

- [ ] N-021 | Priority: P1 | Status: Proposed
  Enable real embedding generation on create/update and verify vector fallback resolution in inbound matching.

- [ ] N-022 | Priority: P2 | Status: Proposed
  Seed runtime system skill definitions into system/skills/definitions and make seeding file-driven instead of inline literals.

## 5) Code Quality and Maintainability

- [ ] N-023 | Priority: P1 | Status: Proposed
  Replace private attribute access patterns in event consumer with explicit public orchestrator interfaces.

- [ ] N-024 | Priority: P2 | Status: Proposed
  Consolidate duplicate task field deserialization utilities used by state and cascade modules.

- [ ] N-025 | Priority: P2 | Status: Proposed
  Replace module-level state machine singleton usage with injected dependency for improved testability.

- [ ] N-026 | Priority: P2 | Status: Proposed
  Correct typing and guard edge cases in scoring and briefing paths (including empty factor handling).

- [ ] N-027 | Priority: P2 | Status: Proposed
  Add structured logging for state-machine guard rejection reasons for operational debugging.

## 6) Validation and Release Gates

- [ ] N-028 | Priority: P0 | Status: Proposed
  For each approved observation: implement code, run targeted tests against real services, then run full lint/format gates.

- [ ] N-029 | Priority: P0 | Status: Proposed
  Require non-mock integration evidence for DB, broker, and storage paths before marking observation as done.

- [ ] N-030 | Priority: P1 | Status: Proposed
  Commit in small batches per approved observation group with test evidence in commit messages.
