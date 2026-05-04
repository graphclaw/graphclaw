# ADR-0003: L7 Agent Evals layer

**Status**: Accepted  
**Date**: 2026-05-04

## Decision

Add a dedicated agent evaluation layer (`tests/agent_evals/`) that uses YAML-defined multi-turn chat scenarios, real LLM calls, behavioral assertions, and LLM-as-judge rubric scoring to guard the main orchestrator (betty) against silent behavioral regressions.

## Context

The orchestrator agent is the heart of the system. Unit and integration tests can verify that individual functions work, but they cannot catch behavioral regressions such as:

- The orchestrator answers inline instead of delegating to a skill.
- The orchestrator loses task context after a tool call.
- The orchestrator loops unnecessarily instead of clarifying once.
- The orchestrator refuses a legitimate request it previously handled correctly.

These regressions are invisible to pytest unit tests because the LLM call is mocked. They only appear when the real model is invoked. No other layer in the pyramid catches them.

## Consequences

- Scenarios are YAML files (`tests/agent_evals/prompts/**/*.yaml`), not Python. Non-engineers can author and review them.
- Assertions are behavioral, not string equality: `tool_called`, `tool_args_match`, `response_does_not_contain`, `latency_ms_under`, `judge_score_above`.
- LLM-as-judge (`claude-sonnet-4-6`) evaluates open-ended responses against a markdown rubric.
- Tests are non-deterministic. They are expected to flake occasionally. A single failure is noise; consistent failure across 3 runs is signal.
- Cost: max $0.05 per canary run, $0.50 per full nightly run. Enforced in `conftest.py` via token budget checks.
- CI gate: canary runs on PRs that touch `src/agent/**`, `src/skills/**`, `src/llm/**`, or `tests/agent_evals/prompts/**`. Full suite runs nightly at 02:00 UTC.
- The `--run-evals` flag gates these tests, analogous to `--run-integration`. They do not run in normal `pytest tests/` invocations.
