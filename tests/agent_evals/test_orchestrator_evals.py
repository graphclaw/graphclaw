# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
GC-A-ORC-W12-001 — Orchestrator behavioral eval suite

Scenario: Parametrized over YAML scenarios in prompts/orchestrator/.
Each scenario is a multi-turn chat session with behavioral assertions and,
for scenarios with a rubric, an LLM-as-judge quality score.

PRD: docs/prd/05-orchestrator.md §AC-5.*
Build wave: W12
Layer: L7 Agent Evals
Owner: agent-team
Last reviewed: 2026-05-04

Cases covered:
- GC-A-ORC-W12-001 Orchestrator creates a task via graph tool
- GC-A-ORC-W12-002 Orchestrator delegates email-drafting to email skill
- GC-A-ORC-W12-003 Orchestrator processes inbound context and updates task status
- GC-A-ORC-W12-004 Orchestrator reads scoring and explains task priority
- GC-A-ORC-W12-005 Orchestrator asks for clarification before acting on ambiguous request
- GC-A-ORC-W12-006 Orchestrator refuses clearly out-of-scope request

Notes:
- Gated by --run-evals flag. Run: pytest tests/agent_evals --run-evals
- Canary scenarios (001, 002, 003) also run on PRs touching src/agent/, src/skills/, src/llm/
- Full suite runs nightly at 02:00 UTC via .github/workflows/nightly-evals.yml
- Budget: $0.50 per nightly run, $0.05 per canary subset run
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent_evals.runners.assertions import run_turn_assertions
from tests.agent_evals.runners.chat_session import load_scenario
from tests.agent_evals.runners.judge import judge_session

PROMPTS_DIR = Path(__file__).parent / "prompts" / "orchestrator"

SCENARIOS = sorted(PROMPTS_DIR.glob("*.yaml"))

pytestmark = [pytest.mark.agent_eval, pytest.mark.slow]


def _canary_id(scenario_path: Path) -> bool:
    """Return True if this scenario is marked canary=true in YAML."""
    import yaml
    raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    return bool(raw.get("canary", False))


@pytest.mark.parametrize("scenario_path", SCENARIOS, ids=lambda p: p.stem)
async def test_orchestrator_scenario(
    scenario_path: Path,
    session_factory,
    eval_reporter,
    request: pytest.FixtureRequest,
) -> None:
    scenario = load_scenario(scenario_path)

    # Dynamically add eval_canary marker for canary scenarios
    if scenario.canary:
        request.node.add_marker(pytest.mark.eval_canary)
    session = session_factory(scenario)

    for turn in scenario.turns:
        result = await session.send(turn.user)
        run_turn_assertions(turn.assert_, result)
        session.check_budget()

    # LLM-as-judge rubric scoring (when configured)
    judge_score = None
    if scenario.rubric:
        verdict = await judge_session(
            transcript=session.transcript,
            rubric_config=scenario.rubric,
            anthropic_client=None,  # uses ANTHROPIC_API_KEY env
        )
        judge_score = verdict.score
        assert verdict.score >= scenario.rubric.pass_threshold, (
            f"Judge score {verdict.score:.2f} below threshold {scenario.rubric.pass_threshold}.\n"
            f"Feedback: {verdict.feedback}"
        )

    eval_reporter.record(
        scenario_id=scenario.id,
        transcript=session.transcript,
        passed=True,
        score=judge_score,
    )
