# GraphClaw Phase 0 — Test Results Baseline

**Date:** 2026-03-18
**Python:** 3.10.11 (Windows) / 3.12 (Docker)
**Database:** Apache AGE 1.7.0 + pgvector 0.8.2 on PostgreSQL 18
**Test Framework:** pytest 9.0.2 + pytest-asyncio 1.3.0

---

## Summary

| Category | Tests | Passed | Failed | Skipped |
|----------|-------|--------|--------|---------|
| Domain Models (`test_models/`) | 42 | 42 | 0 | 0 |
| Scoring Factors (`test_scoring/test_factors.py`) | 41 | 41 | 0 | 0 |
| Scoring Engine (`test_scoring/test_engine.py`) | 11 | 11 | 0 | 0 |
| State Machine (`test_state/test_machine.py`) | 16 | 16 | 0 | 0 |
| Cascade Logic (`test_state/test_cascade.py`) | 12 | 12 | 0 | 0 |
| CLI Commands (`test_cli/test_commands.py`) | 29 | 29 | 0 | 0 |
| Agent Loop (`test_agent/test_loop.py`) | 18 | 18 | 0 | 0 |
| DB Integration (`test_db/test_graph_repository.py`) | 15 | 15 | 0 | 0 |
| **Total** | **211** | **211** | **0** | **0** |

**Result: ALL 211 TESTS PASSING**

---

## Test Details by Module

### 1. Domain Models — `tests/test_models/test_nodes.py` (42 tests)

#### ID Pattern Validation (11 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_task_id_valid_formats` | Accepts TSK-xxx, COMP-xxx, DEL-xxx patterns | PASS |
| `test_task_id_invalid_formats` | Rejects malformed task IDs | PASS |
| `test_user_id_valid` | Accepts USR-xxx format | PASS |
| `test_user_id_invalid` | Rejects malformed user IDs | PASS |
| `test_goal_id_valid` | Accepts GOL-xxx format | PASS |
| `test_constraint_id_valid` | Accepts CON-xxx format | PASS |
| `test_resource_id_valid` | Accepts RES-xxx format | PASS |
| `test_edge_id_valid` | Accepts EDG-xxx format | PASS |
| `test_generated_task_ids_match_all_types` | All 11 task types generate valid IDs | PASS |

#### Enum Completeness (7 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_task_state_has_10_values` | TaskState enum has exactly 10 states | PASS |
| `test_task_state_expected_values` | All 10 state names match PRD | PASS |
| `test_task_type_has_11_values` | TaskType enum has exactly 11 types | PASS |
| `test_task_type_expected_values` | All 11 type names match PRD | PASS |
| `test_edge_type_has_at_least_8_values` | EdgeType covers required relationships | PASS |
| `test_gate_type_has_and_or` | GateType has AND and OR | PASS |
| `test_goal_priority_values` | GoalPriority has P1, P2, P3, P4 | PASS |

#### TaskNode Construction (7 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_atomic_task_defaults` | Default state=PENDING, history=[] | PASS |
| `test_task_with_state_history` | State history entries stored correctly | PASS |
| `test_task_id_validator_rejects_invalid` | Pydantic validator catches bad IDs | PASS |
| `test_task_with_scoring_block` | Scoring override fields serialize | PASS |
| `test_task_with_timeline` | Deadline and effort_estimate_hours work | PASS |
| `test_task_with_tags` | Tags list stored on task | PASS |
| `test_all_task_types_can_be_constructed` | All 11 TaskType variants instantiate | PASS |

#### Type-Specific Metadata (12 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_atomic_metadata` | AtomicMetadata with effort estimate | PASS |
| `test_delegated_metadata` | DelegatedMetadata with assignee | PASS |
| `test_followup_metadata` | FollowupMetadata with trigger | PASS |
| `test_approval_metadata` | ApprovalMetadata with approver list | PASS |
| `test_composite_metadata_gate_defaults_to_and` | Default gate=AND | PASS |
| `test_composite_metadata_or_gate` | OR gate configuration | PASS |
| `test_milestone_metadata` | MilestoneMetadata with target date | PASS |
| `test_review_metadata` | ReviewMetadata with reviewer | PASS |
| `test_recurring_metadata` | RecurringMetadata with cron expression | PASS |
| `test_decision_metadata` | DecisionMetadata with options | PASS |
| `test_checkin_metadata` | CheckinMetadata with frequency | PASS |
| `test_research_metadata` | ResearchMetadata with hypothesis | PASS |
| `test_discriminated_union_round_trip` | Metadata JSON round-trip preserves type | PASS |

#### Other Node Types (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_user_node_defaults` | UserNode construction and defaults | PASS |
| `test_user_id_validator_rejects_invalid` | User ID validation | PASS |
| `test_scoring_weights_sum_to_one` | Default weights sum ≈ 1.0 | PASS |
| `test_goal_node_defaults` | GoalNode construction | PASS |
| `test_goal_id_validator_rejects_invalid` | Goal ID validation | PASS |

---

### 2. Scoring Factors — `tests/test_scoring/test_factors.py` (41 tests)

#### F1: Timeline Urgency (10 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_overdue_returns_above_1` | Overdue task scores > 1.0 | PASS |
| `test_overdue_with_effort` | Effort estimate increases urgency | PASS |
| `test_due_today` | Due today ≈ 0.9 | PASS |
| `test_due_in_2_days` | 2 days out ≈ 0.7–0.8 | PASS |
| `test_due_in_5_days` | 5 days out ≈ 0.4–0.6 | PASS |
| `test_due_in_10_days` | 10 days out ≈ 0.2–0.4 | PASS |
| `test_far_out` | 30+ days ≈ 0.05 | PASS |
| `test_tight_slack_adds_adjustment` | Slack < effort → positive adj | PASS |
| `test_negative_slack_adds_large_adjustment` | Negative slack → large adj | PASS |
| `test_plenty_of_slack` | Ample slack → no adj | PASS |

#### F2: Dependency Weight (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_no_dependents` | Zero dependents → 0.0 | PASS |
| `test_only_direct` | Direct-only fan-out scoring | PASS |
| `test_only_transitive` | Transitive-only scoring | PASS |
| `test_mixed` | Combined direct + transitive | PASS |
| `test_large_fan_out` | High fan-out capped at 1.0 | PASS |

#### F3: Critical Path Score (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_on_critical_path_p1` | CP + P1 → 1.0 | PASS |
| `test_on_critical_path_p2` | CP + P2 → 0.7 | PASS |
| `test_on_critical_path_p3` | CP + P3 → 0.4 | PASS |
| `test_off_critical_path` | Off CP → 0.0 | PASS |
| `test_accepts_string_priority` | String enum values accepted | PASS |

#### F4: Blocker Score (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_hard_blocker` | Hard blocker → 1.0 | PASS |
| `test_soft_blocker` | Soft blocker → 0.5 | PASS |
| `test_no_blocker` | No blocker → 0.0 | PASS |
| `test_string_hard` | String "hard" accepted | PASS |
| `test_unknown_returns_zero` | Unknown type → 0.0 | PASS |

#### F5: Human Override Score (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_prioritize` | Prioritize → 1.0 | PASS |
| `test_deprioritize` | Deprioritize → -0.5 | PASS |
| `test_snooze_returns_none` | Snooze → None (excluded) | PASS |
| `test_string_prioritize` | String "prioritize" accepted | PASS |
| `test_unknown_returns_zero` | Unknown → 0.0 | PASS |

#### F6: Resource Risk (4 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_perfect_resource` | Reliability=1.0, load=0.0 → 0.0 | PASS |
| `test_worst_case` | Reliability=0.0, load=1.0 → 1.0 | PASS |
| `test_medium_risk` | Mid-range values → ~0.5 | PASS |
| `test_high_reliability_low_load` | Good resource → low score | PASS |

#### F7: Constraint Pressure (7 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_no_constraints` | Empty constraints → 0.0 | PASS |
| `test_single_constraint_half_used` | 50% utilized → ~0.25 | PASS |
| `test_constraint_at_limit` | At threshold → 1.0 | PASS |
| `test_constraint_exceeded` | Exceeded → capped at 1.0 | PASS |
| `test_multiple_constraints` | Max of all constraints | PASS |
| `test_zero_threshold_skipped` | Zero threshold safely ignored | PASS |
| `test_missing_keys_skipped` | Incomplete constraint data ignored | PASS |

---

### 3. Scoring Engine — `tests/test_scoring/test_engine.py` (11 tests)

| Test | Description | Status |
|------|-------------|--------|
| `test_returns_score_explanation` | Returns ScoreExplanation model | PASS |
| `test_all_factors_present` | All 7 factors in breakdown | PASS |
| `test_weights_sum_to_1` | Weight vector sums to 1.0 | PASS |
| `test_weighted_scores_match_raw_times_weight` | w_i * raw_i == weighted_i | PASS |
| `test_final_score_is_sum_of_weighted` | Σ weighted_i == final_score | PASS |
| `test_cp_p1_applies_1_5x` | Critical path P1 → 1.5× multiplier | PASS |
| `test_off_cp_no_modifier` | Off critical path → no multiplier | PASS |
| `test_cache_hit_returns_same_result` | Second call returns cached score | PASS |
| `test_cache_invalidation_forces_rescore` | Invalidate → fresh computation | PASS |
| `test_high_dependencies_increase_score` | More dependents → higher score | PASS |
| `test_hard_blocker_increases_score` | Hard blocker → higher score | PASS |

---

### 4. State Machine — `tests/test_state/test_machine.py` (16 tests)

#### Valid Transitions (8 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_pending_to_active` | PENDING → ACTIVE | PASS |
| `test_active_to_in_progress` | ACTIVE → IN_PROGRESS | PASS |
| `test_in_progress_to_complete` | IN_PROGRESS → COMPLETE | PASS |
| `test_pending_to_cancelled` | PENDING → CANCELLED | PASS |
| `test_active_to_blocked` | ACTIVE → BLOCKED | PASS |
| `test_blocked_to_active_by_cascade` | BLOCKED → ACTIVE (cascade trigger) | PASS |
| `test_needs_review_to_complete` | NEEDS_REVIEW → COMPLETE | PASS |
| `test_snoozed_to_active` | SNOOZED → ACTIVE | PASS |

#### Invalid Transitions (4 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_pending_cannot_go_to_complete` | PENDING → COMPLETE rejected | PASS |
| `test_in_progress_cannot_go_to_pending` | IN_PROGRESS → PENDING rejected | PASS |
| `test_active_cannot_go_to_inactive_pending` | ACTIVE → INACTIVE_PENDING rejected | PASS |
| `test_delayed_cannot_go_to_complete` | DELAYED → COMPLETE rejected | PASS |

#### Terminal & Guard Tests (4 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_cancelled_is_terminal` | CANCELLED blocks all transitions | PASS |
| `test_complete_is_terminal` | COMPLETE blocks most transitions | PASS |
| `test_complete_to_needs_review_allowed_for_low_confidence` | Low-confidence reopening | PASS |
| `test_complete_to_needs_review_blocked_for_high_confidence` | High-confidence stays complete | PASS |

#### Approval & Activation Guards (5 tests)
| Test | Description | Status |
|------|-------------|--------|
| `test_approval_task_requires_human_to_complete` | Approval needs human actor | PASS |
| `test_approval_task_completed_by_human_ok` | Human can complete approval | PASS |
| `test_approval_task_completed_by_cascade_rejected` | Cascade can't complete approval | PASS |
| `test_inactive_pending_to_active_by_cascade` | Cascade activates inactive | PASS |
| `test_inactive_pending_to_active_by_human` | Human activates inactive | PASS |

---

### 5. Cascade Logic — `tests/test_state/test_cascade.py` (12 tests)

| Test | Description | Status |
|------|-------------|--------|
| `test_all_children_complete_triggers_parent_complete` | AND gate: all done → parent done | PASS |
| `test_incomplete_children_blocks_parent` | AND gate: partial → parent stays | PASS |
| `test_one_child_complete_triggers_parent_complete` | OR gate: one done → parent done | PASS |
| `test_no_children_complete_blocks_parent` | OR gate: none done → parent stays | PASS |
| `test_low_confidence_research_child_halts_cascade` | Low confidence halts cascade | PASS |
| `test_high_confidence_does_not_halt` | High confidence allows cascade | PASS |
| `test_pending_approval_blocks_auto_complete` | Pending approval blocks cascade | PASS |
| `test_pending_review_blocks_auto_complete` | Pending review blocks cascade | PASS |
| `test_auto_complete_off_skips_cascade` | auto_complete=false disables | PASS |
| `test_complete_parent_is_skipped` | Already complete → no-op | PASS |
| `test_cancelled_parent_is_skipped` | Cancelled → no-op | PASS |
| `test_atomic_parent_does_not_cascade` | Non-composite → no cascade | PASS |

---

### 6. CLI Commands — `tests/test_cli/test_commands.py` (29 tests)

| Test | Description | Status |
|------|-------------|--------|
| `test_task_list_asyncio_run_called` | `task list` invokes async handler | PASS |
| `test_task_list_with_state_filter` | `--state` flag filters correctly | PASS |
| `test_task_list_state_flag_short` | `-s` short flag works | PASS |
| `test_task_list_exception_exits_nonzero` | DB error → exit code 1 | PASS |
| `test_task_show_delegates_to_asyncio_run` | `task show` delegates properly | PASS |
| `test_task_show_requires_task_id` | Missing ID → usage error | PASS |
| `test_task_create_delegates_to_asyncio_run` | `task create` delegates properly | PASS |
| `test_task_create_requires_title` | Missing title → usage error | PASS |
| `test_task_transition_delegates_to_asyncio_run` | `task transition` works | PASS |
| `test_task_transition_requires_both_args` | Missing args → usage error | PASS |
| `test_agent_score_calls_asyncio_run` | `agent score` invokes handler | PASS |
| `test_agent_score_top_n_option` | `--top-n` parameter works | PASS |
| `test_agent_run_calls_asyncio_run` | `agent run` invokes handler | PASS |
| `test_agent_briefing_calls_asyncio_run` | `agent briefing` invokes handler | PASS |
| `test_graph_stats_calls_asyncio_run` | `graph stats` invokes handler | PASS |
| `test_graph_query_requires_cypher_arg` | Missing Cypher → usage error | PASS |
| `test_graph_query_with_cypher` | Cypher arg passed through | PASS |
| `test_list_tasks_calls_list_nodes` | Async helper calls repo | PASS |
| `test_list_tasks_with_state_filter_passes_filter` | State filter forwarded | PASS |
| `test_list_tasks_empty_does_not_raise` | Empty result is safe | PASS |
| `test_list_tasks_no_db_url_exits` | Missing DB URL → exit 1 | PASS |
| `test_show_task_found_calls_get_node` | Show calls get_node | PASS |
| `test_show_task_not_found_exits_with_code_1` | Not found → exit 1 | PASS |
| `test_score_async_calls_run_cycle` | Score handler calls engine | PASS |
| `test_score_async_empty_queue_does_not_raise` | Empty queue is safe | PASS |
| `test_run_cycle_async_calls_run_cycle` | Run handler calls loop | PASS |
| `test_briefing_async_calls_generate_briefing` | Briefing handler calls loop | PASS |
| `test_stats_async_queries_all_labels` | Stats queries all node labels | PASS |
| `test_stats_async_no_db_url_exits` | Missing DB URL → exit 1 | PASS |

---

### 7. Agent Loop — `tests/test_agent/test_loop.py` (18 tests)

| Test | Description | Status |
|------|-------------|--------|
| `test_run_cycle_returns_empty_when_no_tasks` | Empty graph → empty queue | PASS |
| `test_run_cycle_returns_sorted_queue` | Tasks sorted by score descending | PASS |
| `test_run_cycle_filters_terminal_states` | COMPLETE/CANCELLED excluded | PASS |
| `test_run_cycle_db_failure_returns_empty` | DB error → graceful empty | PASS |
| `test_context_defaults_when_no_edges` | No edges → safe defaults | PASS |
| `test_context_picks_up_goal_priority` | Goal P1 → context.goal_priority | PASS |
| `test_context_counts_direct_dependents` | Fan-out counted in context | PASS |
| `test_context_detects_hard_blocker` | Blocker edge → context flag | PASS |
| `test_context_resource_reliability_from_node` | Resource reliability propagated | PASS |
| `test_context_graph_repo_is_set` | GraphRepository passed to context | PASS |
| `test_context_multiple_tasks` | Batch context building works | PASS |
| `test_briefing_includes_rank_and_action` | Briefing has rank + action | PASS |
| `test_briefing_empty_queue` | Empty queue → empty briefing | PASS |
| `test_briefing_respects_top_n` | top_n parameter respected | PASS |
| `test_briefing_contains_score` | Score included in briefing | PASS |
| `test_briefing_contains_autonomy_level` | Autonomy level in briefing | PASS |
| `test_briefing_includes_total_queue_size` | Queue size in briefing | PASS |
| `test_constructor_stores_dependencies` | AgentLoop stores injected deps | PASS |

---

### 8. DB Integration — `tests/test_db/test_graph_repository.py` (15 tests)

**Environment:** Live Postgres 18 + Apache AGE 1.7.0 + pgvector 0.8.2 (Docker)

| Test | Description | Status |
|------|-------------|--------|
| `test_create_returns_properties` | CREATE vertex returns props | PASS |
| `test_get_node_returns_inserted_data` | MATCH by id retrieves node | PASS |
| `test_get_node_missing_returns_none` | Missing id → None | PASS |
| `test_update_node_changes_property` | SET updates properties | PASS |
| `test_delete_node_removes_vertex` | DETACH DELETE removes vertex | PASS |
| `test_create_edge_and_retrieve` | CREATE edge between nodes | PASS |
| `test_get_edges_outgoing` | Outgoing edge query | PASS |
| `test_create_edge_with_properties` | Edge with property payload | PASS |
| `test_list_all_by_label` | List by label returns all | PASS |
| `test_list_with_state_filter` | List with WHERE filter | PASS |
| `test_list_nodes_empty_when_none_match` | No matches → empty list | PASS |
| `test_direct_dependent` | 1-hop downstream query | PASS |
| `test_transitive_dependent` | Multi-hop downstream query | PASS |
| `test_no_dependents` | Leaf node → empty | PASS |
| `test_anchor_not_in_results` | Anchor excluded from results | PASS |

---

## Known Warnings (non-blocking)

1. **`pytest.mark.integration`** — Custom mark not registered in `pyproject.toml` (cosmetic warning)
2. **Coroutine never-awaited warnings** — CLI test mocks sometimes leave unawaited coroutines (no functional impact)
3. **`PytestUnraisableExceptionWarning`** — KeyError in mock lambda cleanup (no functional impact)

## Environment Notes

- **Windows async policy:** `WindowsSelectorEventLoopPolicy` required for psycopg async on Windows
- **Apache AGE limitation:** No parameterized queries inside `$$` Cypher blocks — all values embedded via string interpolation with `_escape()` helper
- **AGE agtype:** JSON values must use Cypher map literal syntax `{key: val}` instead of `::agtype` cast
- **PG18 Docker:** Volume mount is `/var/lib/postgresql` (not `/var/lib/postgresql/data`)
- **pgvector:** Must build from `main` branch for PG18 compatibility (v0.8.0 tag incompatible)
