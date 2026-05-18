# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
Pytest configuration and fixtures for agent eval tests.

Requires: --run-evals flag (prevents accidental runs on every PR).
Provides: llm_client, session_factory, eval_reporter fixtures.
"""

import json
import os
from collections.abc import Generator
from pathlib import Path

import pytest

# ── --run-evals flag ──────────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    # Adds --run-evals option (already added by tests/conftest.py — skip if present)
    try:
        parser.addoption(
            "--run-evals",
            action="store_true",
            default=False,
            help="Run agent eval tests (requires ANTHROPIC_API_KEY and live services)",
        )
    except ValueError:
        pass  # Already registered by root conftest


# ── Skip marker ───────────────────────────────────────────────────────────────


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-evals", default=False):
        skip_evals = pytest.mark.skip(reason="Pass --run-evals to run agent evals")
        for item in items:
            if "agent_evals" in str(item.fspath):
                item.add_marker(skip_evals)


# ── LLM client fixture ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def llm_client():
    """
    Returns a real Anthropic client connected to the orchestrator.
    Requires ANTHROPIC_API_KEY in environment.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set — skipping eval")

    return anthropic.Anthropic(api_key=api_key)


# ── Session factory ────────────────────────────────────────────────────────────


@pytest.fixture
def session_factory(llm_client):
    """
    Returns a callable that creates an EvalSession for a given scenario.
    """
    from tests.agent_evals.runners.chat_session import EvalSession

    def factory(scenario):
        return EvalSession(scenario=scenario, llm_client=llm_client)

    return factory


# ── Eval reporter ─────────────────────────────────────────────────────────────

REPORTS_DIR = Path(__file__).parent / "reports"


class EvalReporter:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(
        self, scenario_id: str, transcript: list, passed: bool, score: float | None = None
    ) -> None:
        self.records.append(
            {
                "scenario_id": scenario_id,
                "passed": passed,
                "score": score,
                "turns": len(transcript),
            }
        )

    def write_report(self, path: Path) -> None:
        REPORTS_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.records, indent=2), encoding="utf-8")


@pytest.fixture
def eval_reporter() -> Generator[EvalReporter, None, None]:
    reporter = EvalReporter()
    yield reporter
    if reporter.records:
        import datetime

        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        reporter.write_report(REPORTS_DIR / f"eval_run_{ts}.json")
