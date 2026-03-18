# GraphClaw Phase 0 Architect Review

**Date:** 2026-03-18
**Reviewer:** Architect Review Agent (Opus)
**Scope:** All Python source under `src/graphclaw/` (35 files, ~3,200 LOC)
**Perspectives:** Architecture, Best Practices, Security, Simplification

---

## Executive Summary

| Dimension | Score (1-5) | Assessment |
|---|---|---|
| **Overall Modularity** | 4.0 | Clean layered separation; minor DRY violations in DB queries |
| **SOLID Compliance** | 3.5 | SRP well observed; OCP/DIP partially violated in graph_repository |
| **Code Quality** | 3.5 | Good type hints and docstrings; deprecated datetime usage throughout |
| **Security Posture** | 2.5 | Cypher injection mitigated but not eliminated; raw query CLI command is high risk |
| **Simplification Potential** | 3.5 | Scoring engine is well-factored; CLI has significant duplication |

**Overall Health: GOOD for Phase 0 proof-of-concept.** The codebase demonstrates solid architectural thinking with clean layer boundaries, well-designed domain models, and a clear separation of concerns in the scoring engine. The primary risks center on Cypher injection patterns in the DB layer and pervasive use of deprecated `datetime.utcnow()`. These should be addressed before Phase 1.

---

## Module-by-Module Findings

### 1. DB Layer (`db/`)

#### 1.1 `db/connection.py` -- Modularity: 4/5

**Patterns:** Context Manager, Connection Pool Factory
**Strengths:**
- Clean async context manager pattern with `get_connection()`
- AGE session setup correctly re-applied on each checkout
- Proper structured logging with extras

**Issues:**
- [BEST-PRACTICE] `_AGE_SETUP_SQL` constant is defined but never used (lines 19-22). The actual setup calls `_setup_age()` which executes statements individually. Dead code.
- [ARCHITECTURE] `create_pool()` initializes AGE on a single connection (line 70-71) but `get_connection()` also runs `_setup_age()` on every checkout. The initial setup in `create_pool()` is redundant since every connection gets setup on checkout anyway.
- [BEST-PRACTICE] No pool health-check or reconnection strategy. If the DB goes down, the pool will serve dead connections.

#### 1.2 `db/graph_repository.py` -- Modularity: 3/5

**Patterns:** Repository Pattern
**Strengths:**
- Comprehensive CRUD for nodes and edges
- Well-documented escape and serialization helpers
- Proper type coercion in `_to_cypher_value()`

**Issues:**
- [SECURITY/HIGH] Cypher queries are built via f-string interpolation. While `_escape()` handles single quotes and backslashes, the `_to_cypher_map()` function embeds property **keys** directly without any validation (line 387: `f"{key}: {_to_cypher_value(value)}"`). A malicious property key like `id: 'x'}) RETURN n //` could break out of the map literal.
- [SECURITY/HIGH] `delete_edge()` (line 335) interpolates `edge_id` directly into Cypher with zero escaping: `WHERE id(e) = {edge_id}`. If `edge_id` is not strictly numeric, this is injectable.
- [ARCHITECTURE] `_parse_agtype()` and `_escape()` are duplicated across `graph_repository.py`, `dependencies.py`, `critical_path.py`, and `scoring_queries.py` (4 copies total). Violates DRY.
- [SOLID/DIP] The repository directly constructs f-string SQL. A query builder abstraction would improve testability and reduce injection surface.
- [BEST-PRACTICE] `create_node()` accepts `Any` type (line 84) and relies on duck-typing for `model_dump()`. Should accept a protocol or `BaseNode`.
- [BEST-PRACTICE] `list_nodes()` filter keys (line 192: `f"n.{key}"`) are not validated against an allowlist. Property names with special characters could cause query errors or injection.

#### 1.3 `db/queries/dependencies.py` -- Modularity: 4/5

**Strengths:**
- Clean async functions with explicit AGE SQL wrappers
- Good docstrings explaining Cypher path semantics

**Issues:**
- [ARCHITECTURE] `_parse_agtype()`, `_escape()`, and `GRAPH_NAME` are duplicated from `graph_repository.py`. Should be extracted to a shared `db/utils.py`.
- [BEST-PRACTICE] Variable-length path patterns (`*`) with no upper bound (line 69: `[:DEPENDS_ON*]`) risk unbounded recursion on deep or cyclic graphs. The docstring acknowledges this but offers no mitigation.

#### 1.4 `db/queries/critical_path.py` -- Modularity: 4/5

**Strengths:**
- Well-documented algorithm with PRD references
- Correct use of `ORDER BY path_effort DESC` to identify longest path
- The `_extract_nodes_list()` helper handles AGE's nested vertex format correctly

**Issues:**
- [ARCHITECTURE] Same DRY violation: `_parse_agtype()`, `GRAPH_NAME` duplicated.
- [BEST-PRACTICE] Line 87: `result = await conn.execute(query, (goal_id,))` appears to use parameterized queries via `%s`, which is good. However, this is **inconsistent** with `graph_repository.py` which uses `_escape()` + f-strings. The codebase should pick one approach.
- [SIMPLIFICATION] `node_max_effort` dict (lines 112-117) is computed but never consumed downstream. The float calculation per non-critical-path node is dead code.

#### 1.5 `db/queries/scoring_queries.py` -- Modularity: 4/5

**Strengths:**
- Uses `%s` parameterized queries for `user_id` and `task_id` -- the most secure pattern in the codebase
- Clean separation of three focused query functions

**Issues:**
- [ARCHITECTURE] `_parse_agtype()` and `_extract_properties()` are duplicated again (copy #4).
- [BEST-PRACTICE] `_TERMINAL_STATES` and `_SNOOZED_STATE` constants (lines 32-35) duplicate knowledge from `enums.py`. If a state is added to `TaskState`, these constants must be manually synced.

---

### 2. Models (`models/`)

#### 2.1 `models/base.py` -- Modularity: 5/5

**Strengths:**
- Clean ID generation with prefix-based entity identification
- Proper regex validation patterns for all entity types
- `utcnow()` utility correctly uses timezone-aware datetime

**Issues:**
- [SIMPLIFICATION] Six near-identical `generate_*_id()` functions (lines 78-105) could be collapsed into a single `generate_id(prefix: str)` factory.
- [SIMPLIFICATION] Six near-identical `validate_*_id()` functions (lines 118-157) could be a single parametric validator with pattern+message args.
- [BEST-PRACTICE] `_sequence_number()` uses `uuid.uuid4().hex[:4]` converted to int (0-65535) then zero-padded to 4 digits. This can produce 5-digit numbers (e.g., 65535) which the regex expects 4+ digits so it works, but the semantics are misleading (it is not a "sequence" -- it is random).

#### 2.2 `models/enums.py` -- Modularity: 5/5

**Strengths:**
- Comprehensive enum coverage of the PRD domain
- All enums extend `(str, Enum)` enabling direct JSON serialization
- Clean, flat organization

**Issues:** None significant. Well-designed.

#### 2.3 `models/nodes.py` -- Modularity: 4/5

**Strengths:**
- Rich domain model with all PRD-specified fields
- Proper use of sub-models (`Timeline`, `ScoringBlock`, `ProgressBlock`, etc.)
- Discriminated union via `TypeMetadata` on `type_metadata` field
- Comprehensive `__all__` export list

**Issues:**
- [BEST-PRACTICE] `Optional` from `typing` is used (line 8) instead of PEP 604 `X | None` syntax. The codebase is Python 3.12+ per CLAUDE.md, so the newer syntax is preferred.
- [ARCHITECTURE] `TaskNode` is a very large model (~40 fields including sub-models). This is acceptable for a graph node, but consider whether some blocks (scoring, progress, override) should be stored as separate linked nodes in Phase 1.
- [BEST-PRACTICE] `BehavioralModel.responsive_hours` is typed as `list[dict]` (line 232). Should use a typed sub-model for type safety.

#### 2.4 `models/edges.py` -- Modularity: 4/5

**Strengths:**
- Both typed per-edge property models AND a generic `EdgeProperties` model
- Clean discriminated design pattern for edge types

**Issues:**
- [SIMPLIFICATION] Seven edge property models (`SpawnedFromProps`, `AssignedToProps`, `OwnedByProps`, `AppliesToProps`, `InformsProps`, `BranchedFromProps`, `BatchedInProps`) are empty `pass` classes (lines 45-84). While they serve as extension points, they add cognitive overhead. Consider whether they are needed in Phase 0 or if a comment/TODO suffices.
- [ARCHITECTURE] `EdgeProperties` (line 92) is a "god model" that unions all possible edge properties into one flat class. This is convenient but violates single responsibility -- it cannot validate that a DEPENDS_ON edge only has `gate_type` set.

#### 2.5 `models/scoring.py` -- Modularity: 5/5

**Strengths:**
- Clean separation of `ScoreFactor`, `ScoreModifier`, `ScoreExplanation`, and `ActionQueueEntry`
- Good support for explainability per PRD Section 4.7
- Proper nesting (explanation contains factors list)

**Issues:** None significant. Well-designed.

#### 2.6 `models/type_metadata.py` -- Modularity: 5/5

**Strengths:**
- Proper discriminated union using `Annotated[Union[...], Field(discriminator="task_type")]`
- All 11 task type variants have dedicated metadata models
- Clean use of `Literal` types for discriminator values

**Issues:** None significant. Excellent use of Pydantic's discriminated unions.

---

### 3. Scoring (`scoring/`)

#### 3.1 `scoring/engine.py` -- Modularity: 4/5

**Patterns:** Strategy Pattern (via factor functions), Builder (ScoringContext)
**Strengths:**
- Clean separation of factor computation from orchestration
- Factor functions are pure (no I/O) -- excellent for unit testing
- Cache integration with invalidation-aware design
- Good use of dataclass for `ScoringContext`

**Issues:**
- [BEST-PRACTICE/HIGH] `datetime.utcnow()` is used on line 150 and is deprecated since Python 3.12. Should use `datetime.now(timezone.utc)`. The project already has `utcnow()` in `models/base.py` that does the right thing but it is not used here.
- [SIMPLIFICATION] `score_task()` is 170+ lines. The factor computation block (lines 153-304) could be extracted into a `_compute_factors()` method to improve readability.
- [ARCHITECTURE] The critical-path post-multiplier (lines 213-227) duplicates the multiplier map from `factors/critical_path.py` (`_PRIORITY_MULTIPLIER`). Applying it both as a factor AND as a post-multiplier appears to double-count critical path importance.
- [BEST-PRACTICE] Weight parameters (`w1` through `w7`) use positional naming. Named weights (e.g., `w_timeline`, `w_dependencies`) would improve readability.
- [ARCHITECTURE] `score_all()` mutates `task.scoring.chain_urgency_rollup` in place (line 373). Side effects in a scoring function are surprising -- should return modified copies or make mutation explicit in the API contract.

#### 3.2 `scoring/cache.py` -- Modularity: 5/5

**Strengths:**
- Clean invalidation API matching the six PRD triggers
- Good logging on invalidation events
- Simple dict-based implementation appropriate for Phase 0

**Issues:**
- [BEST-PRACTICE] `datetime.utcnow()` on line 120. Same deprecation issue.
- [ARCHITECTURE] No TTL or max-size eviction. For Phase 0 this is acceptable, but the cache grows unbounded.

#### 3.3 `scoring/topology.py` -- Modularity: 3/5

**Strengths:**
- Correct implementation of sequential suppression and urgency rollup
- Good separation from the scoring engine

**Issues:**
- [SIMPLIFICATION/HIGH] `analyze_chain_topology()` is 130 lines with deeply nested logic and multiple early returns (5 identical fallback returns). Extract the "default parallel topology" return into a helper.
- [ARCHITECTURE] N+1 query problem: `analyze_chain_topology()` issues individual `get_node()` and `get_edges()` calls per sibling in a loop (lines 148-171). For a chain of 10 siblings, this is 30+ DB round-trips. Should batch.
- [ARCHITECTURE] `apply_sequential_suppression()` and `urgency_rollup()` both call `analyze_chain_topology()` for every task, which itself does multiple DB queries. This means the topology is analyzed twice per task per scoring cycle. Should cache topology results.

#### 3.4 `scoring/action_queue.py` -- Modularity: 5/5

**Strengths:**
- Clean mapping from task type/state to recommended actions
- Single responsibility: converts scored results into action queue entries

**Issues:** None significant. Well-designed.

#### 3.5 `scoring/factors/*.py` -- Modularity: 5/5

**Strengths:**
- All seven factor functions are pure (no I/O, no DB imports)
- Well-documented with parameter descriptions and return value semantics
- Proper handling of both enum and string inputs (defensive coding)

**Issues:**
- [BEST-PRACTICE] `dependency_weight()` returns an unbounded value (`direct + transitive * 0.5`). With many transitive dependents, this can vastly outweigh other factors. Should consider normalization or capping.
- [BEST-PRACTICE] `constraint_pressure()` computes `(threshold - current_value) / threshold` which gives higher pressure when current_value is LOW (far from threshold). The docstring says constraints approaching their limit should have high pressure, but the formula gives the inverse. This appears to be a **logic bug**: it should be `current_value / threshold` for "percentage used" pressure, not `(threshold - current_value) / threshold` which gives "percentage remaining."

---

### 4. State (`state/`)

#### 4.1 `state/machine.py` -- Modularity: 5/5

**Patterns:** State Pattern, Guard Pattern
**Strengths:**
- Clean transition validation with table lookup + guard checks
- Guards are well-separated as static methods
- Good error messages in `InvalidTransitionError`
- Proper history recording on every transition

**Issues:**
- [BEST-PRACTICE] `datetime.utcnow()` on line 76. Same deprecation issue.
- [ARCHITECTURE] The special case for `COMPLETE -> NEEDS_REVIEW` is handled in `_check_transition_table()` (line 91) rather than in the transition table itself. This makes the transition table incomplete as documentation. Consider adding it to the table with a guard.

#### 4.2 `state/transitions.py` -- Modularity: 5/5

**Strengths:**
- Clear, complete transition table covering all 10 states
- Custom exception with from/to state information

**Issues:** None significant.

#### 4.3 `state/cascade.py` -- Modularity: 4/5

**Strengths:**
- Correct implementation of composite completion with gate evaluation
- Confidence-based halt logic (LOW -> NEEDS_REVIEW)
- Proper upward recursion for grandparent cascades

**Issues:**
- [ARCHITECTURE] Module-level `_sm = StateMachine()` singleton (line 29). While acceptable for Phase 0, this makes testing harder (cannot inject a mock state machine).
- [BEST-PRACTICE] `activate_next_in_chain()` has a redundant local import alias: `from graphclaw.models.nodes import TaskNode as TN` (line 175) with comment "local import to avoid cycles." This suggests a circular dependency design issue that should be resolved structurally.
- [SIMPLIFICATION] Broad `except Exception` on lines 113, 193, 213 swallow all errors with only a warning log. In Phase 1, these should catch specific exceptions.

---

### 5. Agent (`agent/`)

#### 5.1 `agent/loop.py` -- Modularity: 3/5

**Patterns:** Orchestrator/Mediator
**Strengths:**
- Clean `run_cycle()` orchestration of fetch -> context -> score -> return
- Falls back gracefully when individual lookups fail

**Issues:**
- [SIMPLIFICATION/HIGH] `build_scoring_context()` is 150+ lines with deep nesting (up to 5 levels). Each context dimension (goal priority, dependencies, blockers, resources, constraints) should be extracted into its own method.
- [ARCHITECTURE] `build_scoring_context()` issues N*M individual DB queries (one per task per dimension). For 50 tasks, this could be 350+ individual queries. Should use batch queries (the `db/queries/` module already has `get_active_tasks_for_scoring` but it is not used here).
- [BEST-PRACTICE] `_fetch_active_tasks()` defines `_TERMINAL` as a local set inside the method (line 299). This is recomputed on every call. Should be a module-level constant.
- [ARCHITECTURE] The transitive dependent count is approximated by the direct count (line 165: `task_transitive_dependents[tid] = direct_count`). The `db/queries/dependencies.py` module has `get_downstream_dependents()` which does the real traversal, but it is never called from here.

#### 5.2 `agent/briefing.py` -- Modularity: 5/5

**Strengths:**
- Clean, focused formatting function
- No dependencies on DB or state layers

**Issues:** None significant.

---

### 6. CLI (`cli/`)

#### 6.1 `cli/main.py` -- Modularity: 5/5

**Strengths:**
- Clean Typer app composition with four sub-apps
- Minimal entry point with proper `no_args_is_help`

**Issues:** None.

#### 6.2 `cli/task_commands.py` -- Modularity: 2/5

**Issues:**
- [SIMPLIFICATION/HIGH] The DSN lookup + pool creation + error handling boilerplate is duplicated **four times** in this file (lines 41-55, 60-72, 98-105, 140-143) and again in every other CLI module. This is the single worst DRY violation in the codebase. Extract to a shared async context manager (e.g., `async with cli_pool() as (pool, repo): ...`).
- [BEST-PRACTICE] `_get_repo_and_pool()` uses the deprecated `asyncio.get_event_loop().run_until_complete()` pattern (line 50). The async helpers correctly use `asyncio.run()` but this sync helper does not.
- [BEST-PRACTICE] `task_create` hardcodes user initials as `"XX"` (line 166). Should accept from CLI arg or config.
- [ARCHITECTURE] Silent `except Exception: pass` on model validation (line 84) swallows corrupted graph data without any indication.

#### 6.3 `cli/agent_commands.py` -- Modularity: 3/5

**Issues:**
- [SIMPLIFICATION] `_run_cycle_async()` and `_score_async()` are nearly identical (both call `loop.run_cycle()` and `format_action_queue()`). Could share implementation.
- [SIMPLIFICATION] Same DSN boilerplate as `task_commands.py`.

#### 6.4 `cli/graph_commands.py` -- Modularity: 3/5

**Issues:**
- [SECURITY/CRITICAL] `graph query` command (line 75-113) executes user-provided Cypher with **zero sanitization**. While documented as a "dev tool," this is a direct SQL/Cypher injection vector. Should be gated behind `--dangerous-allow-raw` flag AND check `ENVIRONMENT != production`.
- [SIMPLIFICATION] Same DSN boilerplate duplication.

#### 6.5 `cli/goal_commands.py` -- Modularity: 3/5

**Issues:**
- [SIMPLIFICATION] Identical boilerplate pattern as task_commands.py.
- [BEST-PRACTICE] Silent `except Exception: pass` on model validation (line 54).

#### 6.6 `cli/formatters.py` -- Modularity: 5/5

**Strengths:**
- Clean Rich formatting with tables and panels
- Good visual hierarchy in score explanations
- Consistent API across all formatters

**Issues:** None significant. Well-designed presentation layer.

---

### 7. Config (`config.py`)

**Modularity: 4/5**

**Strengths:**
- Clean separation of database and app config
- Proper use of `frozen=True` dataclasses
- Module-level singleton pattern

**Issues:**
- [SECURITY/MEDIUM] `AppConfig.anthropic_api_key` is loaded from environment and stored as a plain string attribute (line 42). While this is standard for env-var config, the value is accessible via `config.app.anthropic_api_key` from anywhere in the codebase. Consider masking in `__repr__`.
- [BEST-PRACTICE] `load_dotenv()` is called at module import time (line 13). This is a side effect on import, which can cause issues in testing.
- [ARCHITECTURE] `DatabaseConfig.dsn` uses `os.environ["DATABASE_URL"]` (line 21) which raises `KeyError` on import if not set. All other fields use `os.getenv()` with defaults. Inconsistent -- the KeyError only triggers when `config.database` is first accessed (cached_property), not at import time, but the error message is cryptic.

---

## Security Findings (Ranked by Severity)

### CRITICAL

| # | Finding | File | Line(s) | Recommendation |
|---|---------|------|---------|----------------|
| S1 | **Raw Cypher CLI command** -- `graph query` executes arbitrary user input directly as SQL/Cypher | `cli/graph_commands.py` | 75-113 | Gate behind `--dangerous-allow-raw` flag; block in production; add prominent warning |

### HIGH

| # | Finding | File | Line(s) | Recommendation |
|---|---------|------|---------|----------------|
| S2 | **Unescaped property keys in Cypher maps** -- `_to_cypher_map()` embeds dict keys directly without validation | `db/graph_repository.py` | 383-388 | Validate keys against `^[a-zA-Z_][a-zA-Z0-9_]*$` regex |
| S3 | **Unescaped edge_id in delete_edge()** -- numeric ID interpolated directly | `db/graph_repository.py` | 335 | Validate `edge_id` is numeric before interpolation |
| S4 | **Unvalidated filter keys in list_nodes()** -- property names embedded directly in Cypher | `db/graph_repository.py` | 192 | Validate filter keys against allowlist |

### MEDIUM

| # | Finding | File | Line(s) | Recommendation |
|---|---------|------|---------|----------------|
| S5 | **API key in plain-text config attribute** | `config.py` | 42 | Mask in `__repr__`/`__str__`; consider `SecretStr` from Pydantic |
| S6 | **No depth limit on variable-length Cypher paths** | `db/queries/dependencies.py` | 69, 101 | Add `*..20` upper bound to prevent DoS on cyclic/deep graphs |
| S7 | **edge_type in get_edges() not validated** | `db/graph_repository.py` | 286 | Validate against `EdgeType` enum values |

### LOW

| # | Finding | File | Line(s) | Recommendation |
|---|---------|------|---------|----------------|
| S8 | **Broad except clauses** swallow errors silently | Multiple CLI files | -- | Catch specific exceptions; log at WARNING+ |
| S9 | **No rate limiting on scoring cycles** | `agent/loop.py` | -- | Add cooldown or rate limit for production |

---

## Recommended Refactoring Priorities for Phase 1

### Priority 1: Security Hardening (Estimated: 2 days)

1. **Extract shared DB utilities** -- Create `db/utils.py` with `_parse_agtype()`, `_escape()`, `_extract_properties()`, and `GRAPH_NAME`. Eliminate all 4 duplicated copies.
2. **Add property key validation** to `_to_cypher_map()` and `list_nodes()` filter keys.
3. **Validate `edge_id`** in `delete_edge()` is numeric.
4. **Gate `graph query` CLI command** behind a safety flag and production check.
5. **Add depth limits** to all variable-length Cypher path patterns.

### Priority 2: Fix Bugs (Estimated: 1 day)

1. **Fix `constraint_pressure()` formula** -- currently computes "remaining capacity" instead of "pressure toward limit." Should be `current_value / threshold`.
2. **Fix critical path double-counting** -- the post-multiplier in `engine.py` (line 213) and the factor function both apply goal-priority scaling. Remove one.
3. **Replace all `datetime.utcnow()`** with `datetime.now(timezone.utc)` (deprecated since Python 3.12). Affected files: `scoring/engine.py`, `scoring/cache.py`, `state/machine.py`.

### Priority 3: DRY / CLI Boilerplate (Estimated: 1 day)

1. **Create `cli/_shared.py`** with an async context manager for pool+repo creation and DSN validation. Replace all 8+ copies of the boilerplate across CLI modules.
2. **Remove dead code**: `_AGE_SETUP_SQL` in `connection.py`, `node_max_effort` computation in `critical_path.py`.

### Priority 4: Performance (Estimated: 2 days)

1. **Batch queries in `build_scoring_context()`** -- replace N*M individual queries with bulk operations. Wire up `db/queries/dependencies.py` and `scoring_queries.py` functions that already exist but are unused.
2. **Cache topology analysis** results in `scoring/topology.py` to avoid duplicate `analyze_chain_topology()` calls.
3. **Add connection pool `configure` callback** for AGE setup instead of re-running it on every checkout.

### Priority 5: Code Quality (Estimated: 1 day)

1. **Collapse ID generators** in `models/base.py` into a single parametric function.
2. **Extract `_compute_factors()`** from `ScoringEngine.score_task()`.
3. **Extract scoring context dimensions** from `AgentLoop.build_scoring_context()` into separate methods.
4. **Replace `Optional[X]`** with `X | None` across `models/nodes.py` (Python 3.12+ syntax).
5. **Make `StateMachine` injectable** in `state/cascade.py` instead of module-level singleton.

---

## Appendix: Pattern Inventory

| Pattern | Where Used | Quality |
|---|---|---|
| Repository | `db/graph_repository.py` | Good -- single class owns all graph CRUD |
| Strategy | `scoring/factors/*.py` | Excellent -- pure functions, easily testable |
| State Machine | `state/machine.py`, `state/transitions.py` | Good -- clean table + guard design |
| Cascade | `state/cascade.py` | Good -- recursive composite completion |
| Builder | `scoring/engine.py` (ScoringContext) | Good -- dataclass with default factories |
| Orchestrator | `agent/loop.py` | Acceptable -- could decompose further |
| Discriminated Union | `models/type_metadata.py` | Excellent -- Pydantic best practice |
| Singleton | `config.py` | Acceptable for Phase 0 |
| Context Manager | `db/connection.py` | Good -- proper async context manager |
| Command | `cli/*.py` (Typer) | Good -- clean sub-app composition |
