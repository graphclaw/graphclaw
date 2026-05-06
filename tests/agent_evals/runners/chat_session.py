"""
Multi-turn chat session driver for agent evals.

Loads YAML scenario files and drives the real orchestrator chat loop,
capturing tool calls and response text for assertion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── Scenario schema ───────────────────────────────────────────────────────────

@dataclass
class TurnAssert:
    tool_called: str | None = None
    tool_not_called: str | None = None
    tool_args_match: dict[str, Any] = field(default_factory=dict)
    response_contains: list[str] = field(default_factory=list)
    response_does_not_contain: list[str] = field(default_factory=list)
    response_matches_regex: str | None = None
    latency_ms_under: int | None = None
    cost_usd_under: float | None = None


@dataclass
class Turn:
    user: str
    assert_: list[TurnAssert] = field(default_factory=list)


@dataclass
class Setup:
    seed_dataset: str = "minimal_v1"
    user: str = "dev@example.com"


@dataclass
class RubricConfig:
    judge_model: str = "claude-sonnet-4-6"
    rubric_file: str = ""
    pass_threshold: float = 0.8


@dataclass
class Budget:
    max_tokens: int = 4000
    max_cost_usd: float = 0.10


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    setup: Setup
    turns: list[Turn]
    rubric: RubricConfig | None
    budget: Budget
    canary: bool = False


def load_scenario(path: Path) -> Scenario:
    """Parse a YAML scenario file into a Scenario dataclass."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    setup_raw = raw.get("setup", {})
    setup = Setup(
        seed_dataset=setup_raw.get("seed_dataset", "minimal_v1"),
        user=setup_raw.get("user", "dev@example.com"),
    )

    turns = []
    for t in raw.get("turns", []):
        asserts = []
        for a in t.get("assert", []) if isinstance(t.get("assert"), list) else (
            [t["assert"]] if isinstance(t.get("assert"), dict) else []
        ):
            asserts.append(TurnAssert(
                tool_called=a.get("tool_called"),
                tool_not_called=a.get("tool_not_called"),
                tool_args_match=a.get("tool_args_match", {}),
                response_contains=a.get("response_contains", []),
                response_does_not_contain=a.get("response_does_not_contain", []),
                response_matches_regex=a.get("response_matches_regex"),
                latency_ms_under=a.get("latency_ms_under"),
                cost_usd_under=a.get("cost_usd_under"),
            ))
        turns.append(Turn(user=t["user"], assert_=asserts))

    rubric = None
    if "rubric" in raw:
        r = raw["rubric"]
        rubric = RubricConfig(
            judge_model=r.get("judge_model", "claude-sonnet-4-6"),
            rubric_file=r.get("rubric_file", ""),
            pass_threshold=r.get("pass_threshold", 0.8),
        )

    budget_raw = raw.get("budget", {})
    budget = Budget(
        max_tokens=budget_raw.get("max_tokens", 4000),
        max_cost_usd=budget_raw.get("max_cost_usd", 0.10),
    )

    return Scenario(
        id=raw["id"],
        title=raw["title"],
        description=raw.get("description", ""),
        setup=setup,
        turns=turns,
        rubric=rubric,
        budget=budget,
        canary=raw.get("canary", False),
    )


# ── Session driver ────────────────────────────────────────────────────────────

@dataclass
class TurnResult:
    user: str
    agent: str
    tool_calls: list[dict[str, Any]]
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


class EvalSession:
    """
    Drives a multi-turn eval scenario against the real orchestrator.

    The session abstracts over the actual LLM call so that tests can:
    - Send a user message and get back agent response + tool calls
    - Accumulate transcript for judge evaluation
    - Track token usage and cost against budget caps
    """

    def __init__(self, scenario: Scenario, llm_client: Any) -> None:
        self.scenario = scenario
        self.llm_client = llm_client
        self.transcript: list[TurnResult] = []
        self.total_tokens = 0
        self.total_cost_usd = 0.0

    async def send(self, user_message: str) -> TurnResult:
        """Send one user turn and return the result."""
        import time
        start = time.perf_counter()

        response = await self.llm_client.send_message(
            user_message,
            context=self.transcript,
        )

        latency_ms = (time.perf_counter() - start) * 1000
        result = TurnResult(
            user=user_message,
            agent=response.text,
            tool_calls=response.tool_calls or [],
            latency_ms=latency_ms,
            input_tokens=getattr(response, "input_tokens", 0),
            output_tokens=getattr(response, "output_tokens", 0),
            cost_usd=getattr(response, "cost_usd", 0.0),
        )
        self.transcript.append(result)
        self.total_tokens += result.input_tokens + result.output_tokens
        self.total_cost_usd += result.cost_usd
        return result

    def check_budget(self) -> None:
        """Raise if token or cost budget exceeded."""
        if self.total_tokens > self.scenario.budget.max_tokens:
            raise RuntimeError(
                f"Budget exceeded: {self.total_tokens} tokens > {self.scenario.budget.max_tokens}"
            )
        if self.total_cost_usd > self.scenario.budget.max_cost_usd:
            raise RuntimeError(
                f"Budget exceeded: ${self.total_cost_usd:.4f} > ${self.scenario.budget.max_cost_usd}"
            )
