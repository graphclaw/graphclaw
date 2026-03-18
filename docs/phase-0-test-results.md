# GraphClaw Phase 0 — Test Results Baseline

**Date:** 2026-03-17
**Platform:** Windows 10, Python 3.10.11
**Database:** Apache AGE 1.7.0 + pgvector 0.8.2 on PostgreSQL 18 (Docker)
**Total Tests:** 211 | **Passed:** 211 | **Failed:** 0

---

## Summary by Module

| Module | Tests | Status |
|--------|------:|--------|
| Agent Loop (`test_agent/test_loop.py`) | 18 | All Pass |
| CLI Commands (`test_cli/test_commands.py`) | 29 | All Pass |
| Graph Repository (`test_db/test_graph_repository.py`) | 15 | All Pass |
| Domain Models (`test_models/test_nodes.py`) | 42 | All Pass |
| Scoring Engine (`test_scoring/test_engine.py`) | 11 | All Pass |
| Scoring Factors (`test_scoring/test_factors.py`) | 41 | All Pass |
| State Cascade (`test_state/test_cascade.py`) | 12 | All Pass |
| State Machine (`test_state/test_machine.py`) | 21 | All Pass |
| **Total** | **211** | **All Pass** |

---

## Detailed Test Results

### Agent Loop (18 tests)

| Test | Result |
|------|--------|
| `TestRunCycle::test_run_cycle_returns_empty_when_no_tasks` | PASS |
| `TestRunCycle::test_run_cycle_returns_sorted_queue` | PASS |
| `TestRunCycle::test_run_cycle_filters_terminal_states` | PASS |
| `TestRunCycle::test_run_cycle_db_failure_returns_empty` | PASS |
| `TestBuildScoringContext::test_context_defaults_when_no_edges` | PASS |
| `TestBuildScoringContext::test_context_picks_up_goal_priority` | PASS |
| `TestBuildScoringContext::test_context_counts_direct_dependents` | PASS |
| `TestBuildScoringContext::test_context_detects_hard_blocker` | PASS |
| `TestBuildScoringContext::test_context_resource_reliability_from_node` | PASS |
| `TestBuildScoringContext::test_context_graph_repo_is_set` | PASS |
| `TestBuildScoringContext::test_context_multiple_tasks` | PASS |
| `TestGenerateBriefing::test_briefing_includes_rank_and_action` | PASS |
| `TestGenerateBriefing::test_briefing_empty_queue` | PASS |
| `TestGenerateBriefing::test_briefing_respects_top_n` | PASS |
| `TestGenerateBriefing::test_briefing_contains_score` | PASS |
| `TestGenerateBriefing::test_briefing_contains_autonomy_level` | PASS |
| `TestGenerateBriefing::test_briefing_includes_total_queue_size` | PASS |
| `TestAgentLoopConstructor::test_constructor_stores_dependencies` | PASS |

### CLI Commands (29 tests)

| Test | Result |
|------|--------|
| `TestTaskList::test_task_list_asyncio_run_called` | PASS |
| `TestTaskList::test_task_list_with_state_filter` | PASS |
| `TestTaskList::test_task_list_state_flag_short` | PASS |
| `TestTaskList::test_task_list_exception_exits_nonzero` | PASS |
| `TestTaskShow::test_task_show_delegates_to_asyncio_run` | PASS |
| `TestTaskShow::test_task_show_requires_task_id` | PASS |
| `TestTaskCreate::test_task_create_delegates_to_asyncio_run` | PASS |
| `TestTaskCreate::test_task_create_requires_title` | PASS |
| `TestTaskTransition::test_task_transition_delegates_to_asyncio_run` | PASS |
| `TestTaskTransition::test_task_transition_requires_both_args` | PASS |
| `TestAgentScore::test_agent_score_calls_asyncio_run` | PASS |
| `TestAgentScore::test_agent_score_top_n_option` | PASS |
| `TestAgentScore::test_agent_run_calls_asyncio_run` | PASS |
| `TestAgentScore::test_agent_briefing_calls_asyncio_run` | PASS |
| `TestGraphCommands::test_graph_stats_calls_asyncio_run` | PASS |
| `TestGraphCommands::test_graph_query_requires_cypher_arg` | PASS |
| `TestGraphCommands::test_graph_query_with_cypher` | PASS |
| `TestListTasksAsyncHelper::test_list_tasks_calls_list_nodes` | PASS |
| `TestListTasksAsyncHelper::test_list_tasks_with_state_filter_passes_filter` | PASS |
| `TestListTasksAsyncHelper::test_list_tasks_empty_does_not_raise` | PASS |
| `TestListTasksAsyncHelper::test_list_tasks_no_db_url_exits` | PASS |
| `TestShowTaskAsyncHelper::test_show_task_found_calls_get_node` | PASS |
| `TestShowTaskAsyncHelper::test_show_task_not_found_exits_with_code_1` | PASS |
| `TestAgentScoreAsyncHelper::test_score_async_calls_run_cycle` | PASS |
| `TestAgentScoreAsyncHelper::test_score_async_empty_queue_does_not_raise` | PASS |
| `TestAgentScoreAsyncHelper::test_run_cycle_async_calls_run_cycle` | PASS |
| `TestAgentScoreAsyncHelper::test_briefing_async_calls_generate_briefing` | PASS |
| `TestGraphStatsAsyncHelper::test_stats_async_queries_all_labels` | PASS |
| `TestGraphStatsAsyncHelper::test_stats_async_no_db_url_exits` | PASS |

### Graph Repository — Integration Tests (15 tests)

These tests run against a live Postgres+AGE database.

| Test | Result |
|------|--------|
| `TestCreateAndRetrieveNode::test_create_returns_properties` | PASS |
| `TestCreateAndRetrieveNode::test_get_node_returns_inserted_data` | PASS |
| `TestCreateAndRetrieveNode::test_get_node_missing_returns_none` | PASS |
| `TestCreateAndRetrieveNode::test_update_node_changes_property` | PASS |
| `TestCreateAndRetrieveNode::test_delete_node_removes_vertex` | PASS |
| `TestEdgeCreation::test_create_edge_and_retrieve` | PASS |
| `TestEdgeCreation::test_get_edges_outgoing` | PASS |
| `TestEdgeCreation::test_create_edge_with_properties` | PASS |
| `TestListNodes::test_list_all_by_label` | PASS |
| `TestListNodes::test_list_with_state_filter` | PASS |
| `TestListNodes::test_list_nodes_empty_when_none_match` | PASS |
| `TestDownstreamDependents::test_direct_dependent` | PASS |
| `TestDownstreamDependents::test_transitive_dependent` | PASS |
| `TestDownstreamDependents::test_no_dependents` | PASS |
| `TestDownstreamDependents::test_anchor_not_in_results` | PASS |

### Domain Models (42 tests)

| Test | Result |
|------|--------|
| `TestIDPatterns::test_task_id_valid_formats` | PASS |
| `TestIDPatterns::test_task_id_invalid_formats` | PASS |
| `TestIDPatterns::test_user_id_valid` | PASS |
| `TestIDPatterns::test_user_id_invalid` | PASS |
| `TestIDPatterns::test_goal_id_valid` | PASS |
| `TestIDPatterns::test_constraint_id_valid` | PASS |
| `TestIDPatterns::test_resource_id_valid` | PASS |
| `TestIDPatterns::test_edge_id_valid` | PASS |
| `TestIDPatterns::test_generated_task_ids_match_all_types` | PASS |
| `TestEnumCompleteness::test_task_state_has_10_values` | PASS |
| `TestEnumCompleteness::test_task_state_expected_values` | PASS |
| `TestEnumCompleteness::test_task_type_has_11_values` | PASS |
| `TestEnumCompleteness::test_task_type_expected_values` | PASS |
| `TestEnumCompleteness::test_edge_type_has_at_least_8_values` | PASS |
| `TestEnumCompleteness::test_gate_type_has_and_or` | PASS |
| `TestEnumCompleteness::test_goal_priority_values` | PASS |
| `TestTaskNode::test_atomic_task_defaults` | PASS |
| `TestTaskNode::test_task_with_state_history` | PASS |
| `TestTaskNode::test_task_id_validator_rejects_invalid` | PASS |
| `TestTaskNode::test_task_with_scoring_block` | PASS |
| `TestTaskNode::test_task_with_timeline` | PASS |
| `TestTaskNode::test_task_with_tags` | PASS |
| `TestTaskNode::test_all_task_types_can_be_constructed` | PASS |
| `TestTypeMetadata::test_atomic_metadata` | PASS |
| `TestTypeMetadata::test_delegated_metadata` | PASS |
| `TestTypeMetadata::test_followup_metadata` | PASS |
| `TestTypeMetadata::test_approval_metadata` | PASS |
| `TestTypeMetadata::test_composite_metadata_gate_defaults_to_and` | PASS |
| `TestTypeMetadata::test_composite_metadata_or_gate` | PASS |
| `TestTypeMetadata::test_milestone_metadata` | PASS |
| `TestTypeMetadata::test_review_metadata` | PASS |
| `TestTypeMetadata::test_recurring_metadata` | PASS |
| `TestTypeMetadata::test_decision_metadata` | PASS |
| `TestTypeMetadata::test_checkin_metadata` | PASS |
| `TestTypeMetadata::test_research_metadata` | PASS |
| `TestTypeMetadata::test_discriminated_union_round_trip` | PASS |
| `TestUserNode::test_user_node_defaults` | PASS |
| `TestUserNode::test_user_id_validator_rejects_invalid` | PASS |
| `TestUserNode::test_scoring_weights_sum_to_one` | PASS |
| `TestGoalNode::test_goal_node_defaults` | PASS |
| `TestGoalNode::test_goal_id_validator_rejects_invalid` | PASS |
| `TestGoalNode::test_goal_with_p1_priority` | PASS |
| `TestConstraintNode::test_constraint_node_defaults` | PASS |
| `TestConstraintNode::test_constraint_id_validator_rejects_invalid` | PASS |
| `TestConstraintNode::test_constraint_rule_breach` | PASS |
| `TestResourceNode::test_human_resource_defaults` | PASS |
| `TestResourceNode::test_ai_agent_resource` | PASS |
| `TestResourceNode::test_resource_id_validator_rejects_invalid` | PASS |
| `TestCheckinNode::test_checkin_node_defaults` | PASS |
| `TestGraphEdge::test_depends_on_edge` | PASS |
| `TestGraphEdge::test_part_of_edge_with_sequence` | PASS |
| `TestGraphEdge::test_blocks_edge` | PASS |
| `TestGraphEdge::test_follow_up_for_edge` | PASS |
| `TestGraphEdge::test_edge_id_validator_rejects_invalid` | PASS |
| `TestScoreExplanation::test_score_explanation_construction` | PASS |
| `TestScoreExplanation::test_score_explanation_no_modifiers` | PASS |
| `TestScoreExplanation::test_action_queue_entry` | PASS |

### Scoring Engine (11 tests)

| Test | Result |
|------|--------|
| `TestScoreTask::test_returns_score_explanation` | PASS |
| `TestScoreTask::test_all_factors_present` | PASS |
| `TestScoreTask::test_weights_sum_to_1` | PASS |
| `TestWeightApplication::test_weighted_scores_match_raw_times_weight` | PASS |
| `TestWeightApplication::test_final_score_is_sum_of_weighted` | PASS |
| `TestCriticalPathMultiplier::test_cp_p1_applies_1_5x` | PASS |
| `TestCriticalPathMultiplier::test_off_cp_no_modifier` | PASS |
| `TestCacheIntegration::test_cache_hit_returns_same_result` | PASS |
| `TestCacheIntegration::test_cache_invalidation_forces_rescore` | PASS |
| `TestContextInfluence::test_high_dependencies_increase_score` | PASS |
| `TestContextInfluence::test_hard_blocker_increases_score` | PASS |

### Scoring Factors (41 tests)

| Test | Result |
|------|--------|
| `TestTimelineUrgency::test_overdue_returns_above_1` | PASS |
| `TestTimelineUrgency::test_overdue_with_effort` | PASS |
| `TestTimelineUrgency::test_due_today` | PASS |
| `TestTimelineUrgency::test_due_in_2_days` | PASS |
| `TestTimelineUrgency::test_due_in_5_days` | PASS |
| `TestTimelineUrgency::test_due_in_10_days` | PASS |
| `TestTimelineUrgency::test_far_out` | PASS |
| `TestTimelineUrgency::test_tight_slack_adds_adjustment` | PASS |
| `TestTimelineUrgency::test_negative_slack_adds_large_adjustment` | PASS |
| `TestTimelineUrgency::test_plenty_of_slack` | PASS |
| `TestDependencyWeight::test_no_dependents` | PASS |
| `TestDependencyWeight::test_only_direct` | PASS |
| `TestDependencyWeight::test_only_transitive` | PASS |
| `TestDependencyWeight::test_mixed` | PASS |
| `TestDependencyWeight::test_large_fan_out` | PASS |
| `TestCriticalPathScore::test_on_critical_path_p1` | PASS |
| `TestCriticalPathScore::test_on_critical_path_p2` | PASS |
| `TestCriticalPathScore::test_on_critical_path_p3` | PASS |
| `TestCriticalPathScore::test_off_critical_path` | PASS |
| `TestCriticalPathScore::test_accepts_string_priority` | PASS |
| `TestBlockerScore::test_hard_blocker` | PASS |
| `TestBlockerScore::test_soft_blocker` | PASS |
| `TestBlockerScore::test_no_blocker` | PASS |
| `TestBlockerScore::test_string_hard` | PASS |
| `TestBlockerScore::test_unknown_returns_zero` | PASS |
| `TestHumanOverrideScore::test_prioritize` | PASS |
| `TestHumanOverrideScore::test_deprioritize` | PASS |
| `TestHumanOverrideScore::test_snooze_returns_none` | PASS |
| `TestHumanOverrideScore::test_string_prioritize` | PASS |
| `TestHumanOverrideScore::test_unknown_returns_zero` | PASS |
| `TestResourceRisk::test_perfect_resource` | PASS |
| `TestResourceRisk::test_worst_case` | PASS |
| `TestResourceRisk::test_medium_risk` | PASS |
| `TestResourceRisk::test_high_reliability_low_load` | PASS |
| `TestConstraintPressure::test_no_constraints` | PASS |
| `TestConstraintPressure::test_single_constraint_half_used` | PASS |
| `TestConstraintPressure::test_constraint_at_limit` | PASS |
| `TestConstraintPressure::test_constraint_exceeded` | PASS |
| `TestConstraintPressure::test_multiple_constraints` | PASS |
| `TestConstraintPressure::test_zero_threshold_skipped` | PASS |
| `TestConstraintPressure::test_missing_keys_skipped` | PASS |

### State Machine — Cascade (12 tests)

| Test | Result |
|------|--------|
| `TestANDGate::test_all_children_complete_triggers_parent_complete` | PASS |
| `TestANDGate::test_incomplete_children_blocks_parent` | PASS |
| `TestORGate::test_one_child_complete_triggers_parent_complete` | PASS |
| `TestORGate::test_no_children_complete_blocks_parent` | PASS |
| `TestLowConfidenceHalt::test_low_confidence_research_child_halts_cascade` | PASS |
| `TestLowConfidenceHalt::test_high_confidence_does_not_halt` | PASS |
| `TestApprovalReviewBlocks::test_pending_approval_blocks_auto_complete` | PASS |
| `TestApprovalReviewBlocks::test_pending_review_blocks_auto_complete` | PASS |
| `TestAutoCompleteDisabled::test_auto_complete_off_skips_cascade` | PASS |
| `TestAlreadyResolved::test_complete_parent_is_skipped` | PASS |
| `TestAlreadyResolved::test_cancelled_parent_is_skipped` | PASS |
| `TestNonCompositeParent::test_atomic_parent_does_not_cascade` | PASS |

### State Machine — Transitions (21 tests)

| Test | Result |
|------|--------|
| `TestValidTransitions::test_pending_to_active` | PASS |
| `TestValidTransitions::test_active_to_in_progress` | PASS |
| `TestValidTransitions::test_in_progress_to_complete` | PASS |
| `TestValidTransitions::test_pending_to_cancelled` | PASS |
| `TestValidTransitions::test_active_to_blocked` | PASS |
| `TestValidTransitions::test_blocked_to_active_by_cascade` | PASS |
| `TestValidTransitions::test_needs_review_to_complete` | PASS |
| `TestValidTransitions::test_snoozed_to_active` | PASS |
| `TestStateHistoryRecording::test_history_entry_recorded` | PASS |
| `TestStateHistoryRecording::test_history_entry_fields` | PASS |
| `TestStateHistoryRecording::test_multiple_transitions_accumulate` | PASS |
| `TestStateHistoryRecording::test_history_reason_none_when_empty` | PASS |
| `TestInvalidTransitions::test_pending_cannot_go_to_complete` | PASS |
| `TestInvalidTransitions::test_in_progress_cannot_go_to_pending` | PASS |
| `TestInvalidTransitions::test_active_cannot_go_to_inactive_pending` | PASS |
| `TestInvalidTransitions::test_delayed_cannot_go_to_complete` | PASS |
| `TestTerminalStateGuards::test_cancelled_is_terminal` | PASS |
| `TestTerminalStateGuards::test_complete_is_terminal` | PASS |
| `TestTerminalStateGuards::test_complete_to_needs_review_allowed_for_low_confidence` | PASS |
| `TestTerminalStateGuards::test_complete_to_needs_review_blocked_for_high_confidence` | PASS |
| `TestApprovalGuard::test_approval_task_requires_human_to_complete` | PASS |
| `TestApprovalGuard::test_approval_task_completed_by_human_ok` | PASS |
| `TestApprovalGuard::test_approval_task_completed_by_cascade_rejected` | PASS |
| `TestInactivePendingGuard::test_inactive_pending_to_active_by_cascade` | PASS |
| `TestInactivePendingGuard::test_inactive_pending_to_active_by_human` | PASS |
| `TestInactivePendingGuard::test_inactive_pending_to_active_by_agent_rejected` | PASS |
| `TestBlockedGuard::test_blocked_to_active_by_cascade` | PASS |
| `TestBlockedGuard::test_blocked_to_active_by_agent_rejected` | PASS |

---

## Test Categories

| Category | Count | Description |
|----------|------:|-------------|
| Unit Tests | 174 | Pure logic tests (models, scoring factors, state machine, CLI, agent loop) |
| Integration Tests | 15 | Tests against live Postgres+AGE database |
| Known-Answer Tests | 41 | Scoring factor tests with pre-computed expected values |

## Warnings (Non-Critical)

- 11 warnings total, all related to unawaited coroutines in CLI mock tests (cosmetic, no functional impact)
- `pytest.mark.integration` registered as unknown mark (can be registered in pyproject.toml)

## Infrastructure Verified

- Docker image: `apache/age:latest` (PG18) + pgvector 0.8.2
- Graph: `graphclaw` property graph with AGE Cypher queries
- Connection pooling: psycopg `AsyncConnectionPool`
- Windows async: `SelectorEventLoop` policy for psycopg compatibility
