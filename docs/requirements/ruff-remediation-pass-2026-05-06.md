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

## Next Batches

1. Batch 2: src datetime import style cleanup
   - Convert one-line timezone alias statements to Ruff-compliant imports
   - Target files currently raising E702 + I001 in src/graphclaw/*
2. Batch 3: remaining import ordering in src routes/apis
3. Batch 4: run full repo check and finalize

## Completion Criteria

- .venv\Scripts\python.exe -m ruff check src tests returns zero violations
- No behavior changes, lint-only diffs
- Each batch lands in a separate commit for easy review
