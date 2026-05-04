# ADR-0004: pytest integration tests vs scripts/ for API testing

**Status**: Accepted  
**Date**: 2026-05-04

## Decision

Backend API testing uses two distinct, non-overlapping tracks:

1. **`tests/integration/` (pytest + httpx)** — quality gate, runs in CI, fails the build.
2. **`scripts/` (Python scripts)** — human dev tooling, NEVER runs in CI.

Scripts must not contain assertions. If a script starts accumulating assertions and becoming a de-facto test suite, it must be promoted: rewrite with fixtures + assertions → move to `tests/integration/` → delete the original script.

## Context

Scripts in `scripts/` (`api_smoke.py`, `chat_repl.py`) are ad-hoc tools for human developers. They print output for eyeballing. They assume seed data exists. They are fragile to environment state. Running them in CI would produce false positives (script ran, nothing asserted), false negatives (seed data stale), or flaky failures (timing assumptions broken).

Pytest integration tests are proper tests: structured assertions, fixture-based setup/teardown, parametrization, coverage measurement, JUnit XML output, failure traces that identify the exact assertion and line number.

Wiring scripts into CI creates a second source of truth for "what counts as a passing build" and leads to duplicate maintenance when endpoints change.

## Consequences

- Decision rule: **should this fail the build if it breaks?** Yes → `tests/integration/`. No → `scripts/`.
- `scripts/api_smoke.py` — for a developer who has just changed auth or routing and wants a quick sanity check against a local stack. Prints JSON. No assertions. Not in CI.
- `scripts/chat_repl.py` — interactive multi-turn chat with the orchestrator. For manual exploration and demos. Not in CI.
- Typer CLI command tests are a separate category: `tests/test_cli/` uses `typer.testing.CliRunner` and is deterministic, fast, and runs in every CI build.
