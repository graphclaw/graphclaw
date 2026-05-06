# Repo-wide Ruff Remediation Pass (Kickoff 2026-05-06)

## Purpose

Start a dedicated, multi-batch Ruff debt remediation stream separate from Wave M closeout commits.

## Baseline

Command:

- .venv\Scripts\python.exe -m ruff check src tests

Initial baseline at kickoff:

- 30 total violations
- 12 auto-fixable
- Main categories:
  - I001 import block sorting
  - E702 multiple statements on one line
  - F401 unused imports
  - UP035/UP037 typing modernizations

## Batch 1 (Completed)

Scope:

- tests/agent_evals/
- tests/contract/test_openapi_schema.py

Commands:

- .venv\Scripts\python.exe -m ruff check --fix tests/agent_evals tests/contract/test_openapi_schema.py
- .venv\Scripts\python.exe -m ruff check tests/agent_evals tests/contract/test_openapi_schema.py

Result:

- 12 issues auto-fixed
- Follow-up check for Batch 1 scope passed cleanly

## Batch 2 (Completed)

Scope:

- src/graphclaw/* modules with E702 + I001 violations
- src/graphclaw/api/router.py import ordering

Commands:

- .venv\Scripts\python.exe -m ruff check src
- .venv\Scripts\python.exe -m ruff check --fix src/graphclaw/api/router.py
- .venv\Scripts\python.exe -m ruff check src
- .venv\Scripts\python.exe -m ruff check src tests

Result:

- Replaced one-line timezone alias import pattern with `from datetime import UTC, ...` across affected source files
- Router import ordering normalized
- `ruff check src` passes
- `ruff check src tests` passes

## Remaining Batches

- None currently required for this remediation pass.

## Completion Criteria

- .venv\Scripts\python.exe -m ruff check src tests returns zero violations
- No behavior changes, lint-only diffs
- Each batch lands in a separate commit for easy review

## Completion Status

- Completed on 2026-05-06
- Current status: `ruff check src tests` clean
