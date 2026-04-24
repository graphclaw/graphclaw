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

- [x] N-011 | Priority: P0 | Status: Completed (Implemented)
  Add execution timeout and cancellation handling in SubAgentRunner loop with explicit timeout events and blocked-state escalation.
  Design details (Implemented):
  - Runtime path: src/graphclaw/agent/sub_agent_runner.py execute().
  - Explicit terminal statuses include COMPLETED, FAILED, TIMED_OUT, CANCELLED.
  - Timeout and cancellation both emit BLOCKED followed by terminal COMPLETED(status=...).
  - Cancellation path records duration and re-raises asyncio.CancelledError for cooperative shutdown.
  - Event consumer BLOCKED handling routes blocked outcomes to BLOCKED task state.
  Completion evidence:
  - Implementation: src/graphclaw/agent/sub_agent_runner.py, src/graphclaw/agent/event_consumer.py.
  - Tests: tests/test_agent/test_sub_agent_orchestration.py::test_execute_times_out_when_runner_exceeds_execution_limit and ::test_execute_emits_cancelled_status_when_task_is_cancelled.

- [x] N-012 | Priority: P1 | Status: Completed (Implemented)
  Add per-call timeout wrappers for worker.execute and MCP client calls with retry policy where safe and idempotent.
  Design details (Implemented):
  - Runtime path: src/graphclaw/agent/sub_agent_runner.py _dispatch_tool().
  - Tool calls are wrapped in per-call asyncio.wait_for timeouts.
  - Retry policy is bounded and allowlist-based (retryable skills + retryable MCP tools).
  - Defaults remain safe-by-default (0 retries unless explicitly configured).
  - Exponential backoff knobs are config-driven via env-backed AgentPoolConfig.
  Completion evidence:
  - Implementation: src/graphclaw/agent/sub_agent_runner.py, src/graphclaw/infra/config.py.
  - Tests: tests/test_agent/test_sub_agent_orchestration.py::test_dispatch_tool_returns_timeout_error_for_slow_tool and ::test_dispatch_tool_retries_retryable_skill.

- [x] N-013 | Priority: P1 | Status: Completed (Implemented)
  Remove dependence on private semaphore internals in SubAgentPool metrics and track active/waiting counts explicitly.
  Design details (Implemented):
  - Runtime path: src/graphclaw/agent/sub_agent_pool.py active_count/queue_depth properties.
  - Explicit counters (_queued_count, _active_count) replace private semaphore internals for metrics.
  - active_count and queue_depth are exposed from explicit tracked state.
  - Concurrency limit still uses semaphore for throttling, but metrics do not read semaphore internals.
  Completion evidence:
  - Implementation: src/graphclaw/agent/sub_agent_pool.py.
  - Tests: tests/test_agent/test_sub_agent_orchestration.py::test_initial_active_count_zero.

- [x] N-014 | Priority: P1 | Status: Completed (Implemented)
  Fail fast for critical startup dependencies (DB, storage, broker) or expose explicit degraded-mode readiness with alarms.
  Design details (Implemented):
  - Startup path: src/graphclaw/gateway/app.py lifespan().
  - Startup mode supports strict and degraded via GRAPHCLAW_STARTUP_MODE.
  - startup_health diagnostics now track broker/database/storage readiness.
  - /health/ready returns structured dependency health payload and degraded status when critical deps are unhealthy.
  - strict mode raises startup failure when critical dependencies are missing.
  Completion evidence:
  - Implementation: src/graphclaw/gateway/app.py.
  - Tests: tests/test_gateway/test_app.py::test_readiness_endpoint_with_broker and ::test_readiness_endpoint_no_broker_returns_503.

- [ ] N-015 | Priority: P1 | Status: Partially Implemented (Validation Complete)
  Add startup/runtime validation to ensure heartbeat interval and timeout values are coherent.
  Design details (Current state):
  - Config path: src/graphclaw/infra/config.py AgentPoolConfig.
  - Implemented:
    - Pydantic invariants for heartbeat, timeout ordering, and retry backoff coherence.
    - Env parsing for retry allowlists and retry timing knobs.
    - Validation matrix tests for AgentPoolConfig.
  - Remaining gap vs planned scope:
    - degraded-mode fallback normalization to safe defaults is not implemented; invalid AgentPoolConfig currently fails sub-agent pool initialization (logged), rather than auto-normalizing values.
  Verification evidence:
  - Implementation: src/graphclaw/infra/config.py, src/graphclaw/gateway/app.py.
  - Tests: tests/test_infra/test_agent_pool_config.py.

## 4) Functional Gaps vs Product Design

- [x] N-016 | Priority: P0 | Status: Completed (Implemented)
  Implement propose-plan review lifecycle: draft plan persistence, human approval/edit stage, then atomic execute-plan commit.
  Design details:
  - Runtime path: src/graphclaw/agent/main_orchestrator.py planning tools.
  - Lifecycle states: DRAFT -> APPROVED -> EXECUTED.
  - Draft persistence:
    - propose_plan now persists plan objects with status DRAFT and revision metadata.
    - persistence uses StorageClient key: {user_id}/agents/{agent_id}/state/pending_plans/{plan_id}.json plus in-memory cache.
  - Human review stage:
    - Added edit_plan tool to patch goal fields and optionally replace tasks.
    - Editing an APPROVED plan resets status back to DRAFT and requires re-approval.
    - Added approve_plan tool to explicitly transition DRAFT -> APPROVED.
  - Execute guardrails:
    - execute_plan now requires status APPROVED and rejects DRAFT/unknown states.
    - optional approved_task_ids allow partial execution by draft_task_id.
  - Atomicity model:
    - execute_plan uses compensating rollback semantics.
    - if any goal/task create step fails, all created nodes from the same execution attempt are deleted in reverse order and plan status remains non-executed.
  - Tool contract alignment:
    - planning toolset now exposes propose_plan, edit_plan, approve_plan, execute_plan.
    - propose_plan accepts either free-form description or goal_or_task_id/context for backward-compatible invocation.
  Completion evidence:
  - Implementation:
    - src/graphclaw/agent/main_orchestrator.py (draft persistence helpers, edit/approve tools, approval-gated execute, rollback logic).
    - src/graphclaw/agent/tool_registry.py (new planning tools and updated schemas/descriptions).
  - Tests:
    - tests/test_agent/test_plan_lifecycle.py (draft persistence, approval gating, edit resets approval, rollback on failure).
    - tests/test_agent/test_tool_registry.py (planning set includes edit_plan and approve_plan).

- [-] N-017 | Priority: P1 | Status: In Progress (Implementation Initiated)
  Implement bottom-up goal inference from task clusters and relationship patterns.
  Design details (approved):
  - Runtime path: src/graphclaw/agent/main_orchestrator.py planning tools.
  - Added storage-backed goal-inference draft lifecycle under
    `{user_id}/agents/{agent_id}/state/pending_goal_inferences/{inference_id}.json`.
  - Added `propose_goal_inference` tool: clusters ungrouped active tasks using relationship patterns
    (shared assignee, deadline window, shared tags/task-type topic), scores confidence, and persists DRAFT proposals.
  - Added `approve_goal_inference` tool: requires explicit inference_id approval, then creates
    GoalNode (`origin=AGENT_INFERRED`, `inferred_from`, `confirmed_by_user=true`) and wires PART_OF edges.
  - Safety behavior: commit path uses compensating rollback (`delete_node(goal_id)`) on failure.
  Implementation progress:
  - Code updated: src/graphclaw/agent/main_orchestrator.py, src/graphclaw/agent/tool_registry.py.
  - Tests added/updated:
    - tests/test_agent/test_goal_inference.py
    - tests/test_agent/test_tool_registry.py

- [-] N-018 | Priority: P1 | Status: In Progress (Implementation Initiated)
  Implement HandoffNode schema and related edge wiring where required by design spec.
  Design details (approved):
  - Added `HandoffNode` coordination schema (`task_id`, `from_owner`, `to_owner`, context payload)
    with dedicated ID format `HND-*` and validator/generator helpers.
  - Added graph schema wiring for coordination linkage:
    - vertex labels: `CheckinNode`, `HandoffNode`
    - edge label: `REFERRED_BY`
    - baseline + forward migration coverage via migration `0007`.
  - Delegation runtime now records ownership transitions:
    - `delegate_to_agent` creates a `HandoffNode` when assignee changes and links it to the task
      via `REFERRED_BY` edge (non-fatal if DB labels are not yet migrated).
  - Check-in linkage aligned to canonical edge label by using `REFERRED_BY`.
  Implementation progress:
  - Code updated: src/graphclaw/models/base.py, src/graphclaw/models/nodes.py,
    src/graphclaw/models/enums.py, src/graphclaw/models/edges.py,
    src/graphclaw/agent/main_orchestrator.py, src/graphclaw/db/age/repository.py,
    scripts/init-db.sql, src/graphclaw/migrations/catalogue.py.
  - Tests added/updated:
    - tests/test_models/test_nodes.py
    - tests/test_agent/test_sub_agent_orchestration.py

- [x] N-019 | Priority: P1 | Status: Completed (Design Decision Locked - Simplified Model)
  Evaluate and implement Dependency Gate as a first-class node if required by final design decision.
  Final decision (2026-04-24):
  - Keep metadata-only gate model (Option 1).
  - Existing semantics via `GateType` and `completion_gate` are accepted as the intentional design.
  Closure note:
  - No first-class DependencyGate node/type will be added in this requirement wave.

- [-] N-020 | Priority: P1 | Status: In Progress (Core Wiring Implemented)
  Connect inbound processor to live channels (email/Slack/etc.) with production-ready ingestion path.
  Acceptance scope decision (2026-04-24):
  - Required channel scope for closure is email only.
  Implementation evidence present in code:
  - Gateway ingress and queue publishing:
    - `src/graphclaw/gateway/app.py` (`/api/v1/inbound`, `/api/v1/trigger`, SES/Slack/Telegram/WhatsApp/Teams webhooks)
    - inbound payloads are published to `INBOUND_MESSAGES`.
  - Channel adapters publish normalized inbound payloads:
    - `src/graphclaw/gateway/channels/*/adapter.py`
    - `src/graphclaw/gateway/channels/email/poller.py`.
  - Trigger/event bridge and processing path:
    - `src/graphclaw/triggers/engine.py` converts `INBOUND_MESSAGES` to trigger events.
    - `src/graphclaw/agent/event_consumer.py` processes inbound messages.
  Validation status:
  - Unit/API tests exist for inbound route publishing and adapter behavior.
  - Remaining for closure: non-mock end-to-end evidence for email ingestion path across DB + broker + storage in one run.

- [x] N-021 | Priority: P1 | Status: Completed (Implemented + Validated)
  Enable real embedding generation on create/update and verify vector fallback resolution in inbound matching.
  Runtime behavior decision (2026-04-24):
  - Fail open when embedding service is unavailable.
  - User-facing behavior must:
    - show that automatic match is unavailable due to embedding service unavailability,
    - present relevant candidate nodes,
    - request manual user selection/input to resolve matching.
  Implemented:
  - Embedding client abstraction available: `src/graphclaw/infra/embeddings.py`.
  - Repository embedding persistence implemented:
    - `src/graphclaw/db/age/repository.py` has background embedding generation on task create/update
      and upsert into `node_embeddings`.
  - Resolver includes vector-search path:
    - `src/graphclaw/inbound/resolver.py` supports embedding-client-driven fallback.
  Completed in this pass:
  - Added fail-open resolution metadata + manual candidate list support:
    - `src/graphclaw/inbound/models.py` (`CandidateNodeMatch`, `match_unavailable_reason`, `candidate_nodes`).
  - Resolver now returns ranked manual candidates when embedding path is unavailable or low confidence:
    - `src/graphclaw/inbound/resolver.py` (`resolve(..., user_id=...)`, `_suggest_candidates`).
  - Processor now emits explicit `manual_match_required` action for fail-open cases:
    - `src/graphclaw/inbound/processor.py`.
  - Event consumer user notification now explains unavailability reason and includes candidate task IDs for manual selection:
    - `src/graphclaw/agent/event_consumer.py`.
  Validation evidence:
  - `pytest tests/test_inbound/test_resolver.py tests/test_inbound/test_processor.py tests/test_agent/test_event_consumer.py -q` -> `50 passed`.
  - Added regression tests:
    - `tests/test_inbound/test_resolver.py::test_resolve_embedding_unavailable_returns_manual_candidates`
    - `tests/test_inbound/test_processor.py::test_process_manual_match_required_when_embedding_unavailable`
    - `tests/test_agent/test_event_consumer.py::test_notify_user_unmatched_includes_manual_match_candidates`

- [x] N-022 | Priority: P2 | Status: Completed
  Seed runtime system skill definitions into system/skills/definitions and make seeding file-driven instead of inline literals.
  Completion details:
  - Seeding moved to file-driven source for prompt header, knowledge, and system agent assets:
    - `src/graphclaw/gateway/seeding.py`
    - `src/graphclaw/gateway/prompts/**`
  - Added file-driven runtime skill-definition seeding from repository source:
    - source: `src/graphclaw/skills/definitions/*/SKILL.md`
    - destination: `system/skills/definitions/{skill}/SKILL.md`
    - implementation: `src/graphclaw/gateway/seeding.py` (`_iter_system_skill_definition_files`).
  - Startup wiring calls seeding during gateway bootstrap:
    - `src/graphclaw/gateway/app.py`.
  - Tests added:
    - `tests/test_gateway/test_seeding.py` (unit, non-integration).
    - `tests/test_gateway/test_seeding_integration.py` extended with skill-definition checks.
  Validation evidence:
  - `pytest tests/test_gateway/test_seeding.py tests/test_models/test_deserialization.py tests/test_scoring/test_factor_guards.py -q` → passed.

  Status refresh (2026-04-24):
  - Recent workspace edits in `state/machine.py`, `tests/test_models/test_deserialization.py`, and `tests/test_gateway/test_seeding.py`
    preserve the implemented behavior for N-022 and do not change completion status.

## 5) Code Quality and Maintainability

- [x] N-023 | Priority: P1 | Status: Completed
  Replace private attribute access patterns in event consumer with explicit public orchestrator interfaces.
  Completion evidence:
  - `src/graphclaw/agent/event_consumer.py` uses dedicated public accessor helpers
    (`_graph_repo`, `_llm_client`, `_agent_id`) and no longer relies on direct private-attribute reach-through.

- [x] N-024 | Priority: P2 | Status: Completed
  Consolidate duplicate task field deserialization utilities used by state and cascade modules.
  Completion evidence:
  - Added shared helper module:
    - `src/graphclaw/models/deserialization.py` (`deserialize_task_node_props`).
  - Replaced duplicate call-sites:
    - `src/graphclaw/api/state.py`
    - `src/graphclaw/state/cascade.py`
    - `src/graphclaw/agent/main_orchestrator.py`.
  - Added tests:
    - `tests/test_models/test_deserialization.py`.

- [x] N-025 | Priority: P2 | Status: Completed
  Replace module-level state machine singleton usage with injected dependency for improved testability.
  Completion evidence:
  - `src/graphclaw/state/cascade.py` resolves to injected `StateMachine` and
    passes it through cascade helpers instead of relying on module singleton state.
  - API and orchestrator call-sites pass explicit `state_machine` dependencies.

- [x] N-026 | Priority: P2 | Status: Completed
  Correct typing and guard edge cases in scoring and briefing paths (including empty factor handling).
  Completion evidence:
  - Defensive factor handling and type normalization in scoring factors:
    - `src/graphclaw/scoring/factors/constraint.py`
    - `src/graphclaw/scoring/factors/blocker.py`
    - `src/graphclaw/scoring/factors/resource_risk.py`.
  - Briefing formatter handles malformed/non-numeric optional values safely:
    - `src/graphclaw/agent/briefing.py`.
  - Added guard tests:
    - `tests/test_scoring/test_factor_guards.py`.

- [x] N-027 | Priority: P2 | Status: Completed
  Add structured logging for state-machine guard rejection reasons for operational debugging.
  Completion evidence:
  - Added guard rejection logger hook with structured fields in
    `src/graphclaw/state/machine.py` (`_log_guard_rejection`) and wired it
    across guard failure branches.
  - Guard rejection reason is emitted both as structured metadata and in log message text,
    with test coverage in `tests/test_state/test_machine.py`.

## 6) Validation and Release Gates

- [-] N-028 | Priority: P0 | Status: In Progress (Gates Executed, Partial Blockers)
  For each approved observation: implement code, run targeted tests against real services, then run full lint/format gates.
  Gate policy decision (2026-04-24):
  - Strict full-repo gates must pass before closure.
  Current evidence:
  - Targeted validation completed for N-022..N-027 scope:
    - `pytest tests/test_gateway/test_seeding.py tests/test_models/test_deserialization.py tests/test_scoring/test_factor_guards.py tests/test_state/test_machine.py tests/test_agent/test_event_consumer.py -q` → `57 passed`.
    - focused lint passed on touched files (`ruff check ...`).
    - focused format check passed on touched files (`ruff format --check ...`).
  - Full workspace gates remain blocked by repository-wide/environment issues outside the N-022..N-027 patch surface.

  Status refresh (2026-04-24):
  - Latest recorded full-suite runs (see `test_results_new.txt` / `test_results_final.txt`) indicate
    broad integration failures tied to environment setup (storage auth and DB test infra), so N-028
    remains in progress pending stable full-gate execution in a clean environment.

- [-] N-029 | Priority: P0 | Status: In Progress (Mandatory Precheck Implemented)
  Require non-mock integration evidence for DB, broker, and storage paths before marking observation as done.
  Test execution decision (2026-04-24):
  - Required services-up precheck is mandatory before running integration suites.
  Implemented in this pass:
  - Added centralized readiness probe helper for integration dependencies:
    - `tests/integration_precheck.py` (DB + Redis + storage checks).
  - Added CLI precheck script for manual/operator use:
    - `scripts/precheck_services.py`.
  - Enforced integration gate in pytest collection flow:
    - `tests/conftest.py` adds `--run-integration` / `GRAPHCLAW_RUN_INTEGRATION=1` control and
      runs mandatory services precheck before executing `@pytest.mark.integration` tests.
    - Integration tests are skipped unless explicitly enabled; when enabled and services are down,
      pytest exits with a precheck failure message.
  - Added unit coverage:
    - `tests/test_infra/test_integration_precheck.py`.
  Evidence collected:
  - Broker path (live Redis) succeeded:
    - `pytest tests/test_infra/test_user_events.py -q` → `10 passed`.
  - Precheck runtime behavior validated:
    - `python scripts/precheck_services.py` → explicit FAIL with dependency-specific reasons when services are down.
  - Integration gate behavior validated:
    - `pytest tests/test_db/test_graph_repository.py -q` → integration tests skipped by default unless enabled.
  Blockers observed in current environment:
  - DB integration path failed to initialize pool:
    - `pytest tests/test_db/test_graph_repository.py -q` → `psycopg_pool.PoolTimeout` during test DB pool setup.
  - Storage integration path is blocked by object-storage credential/access issues in latest runs
    (`InvalidAccessKeyId` / `403 Forbidden` against seeded system paths).
  Remaining requirement:
  - Collect green, non-mock evidence for DB + broker + storage in the same environment after mandatory services-up precheck.

- [-] N-030 | Priority: P1 | Status: In Progress (Commit Policy Locked)
  Commit in small batches per approved observation group with test evidence in commit messages.
  Commit evidence decision (2026-04-24):
  - Each commit/batch summary must include explicit pass/fail summary for executed validation commands.
  Current evidence gap:
  - No observation-scoped commit batch sequence with pass/fail evidence summary has been executed in this pass yet.

## Status Snapshot (2026-04-24)

- N-019: Completed (metadata-only gate model accepted as intentional simplification).
- N-020: In Progress (email-only acceptance scope locked; full non-mock closure pending).
- N-021: Completed (fail-open + manual match candidate UX implemented and validated).
- N-022: Completed.
- N-023: Completed.
- N-024: Completed.
- N-025: Completed.
- N-026: Completed.
- N-027: Completed.
- N-028: In Progress (strict full-repo gate policy locked; full-suite pass not yet achieved).
- N-029: In Progress (services-up precheck now enforced; DB/storage non-mock green evidence still pending).
- N-030: In Progress (commit evidence policy locked; pass/fail summary still to be applied in commit batches).
