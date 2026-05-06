"""
LLM-as-judge for agent eval rubric scoring.

Takes a multi-turn transcript and a rubric markdown file, sends both to the
judge model, and returns a 0-1 score with feedback text.

Cost cap: judge calls are billed against the eval run's budget. The judge
model default is claude-sonnet-4-6 (cheaper than Opus, sufficient for rubric scoring).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chat_session import RubricConfig, TurnResult

RUBRICS_DIR = Path(__file__).parent.parent / "rubrics"

JUDGE_SYSTEM_PROMPT = """\
You are an objective evaluator assessing the quality of an AI agent's responses.
You will receive:
1. A rubric defining evaluation criteria
2. A conversation transcript between a user and an AI agent

Score the agent's performance from 0.0 to 1.0 based on the rubric.
Respond with ONLY a JSON object in this exact format:
{"score": <float 0.0-1.0>, "feedback": "<one paragraph explanation>"}
"""


@dataclass
class JudgeVerdict:
    score: float
    feedback: str
    raw_response: str = ""


async def judge_session(
    transcript: list[TurnResult],
    rubric_config: RubricConfig,
    anthropic_client: object | None = None,
) -> JudgeVerdict:
    """
    Score a transcript against a rubric using an LLM-as-judge.

    Args:
        transcript: List of TurnResult from EvalSession.
        rubric_config: RubricConfig with judge_model, rubric_file, pass_threshold.
        anthropic_client: Optional pre-configured Anthropic client. If None,
                          creates one from ANTHROPIC_API_KEY env var.

    Returns:
        JudgeVerdict with score (0-1) and feedback text.
    """
    import json

    import anthropic

    # Load rubric text
    rubric_path = RUBRICS_DIR / rubric_config.rubric_file
    if not rubric_path.exists():
        raise FileNotFoundError(f"Rubric file not found: {rubric_path}")
    rubric_text = rubric_path.read_text(encoding="utf-8")

    # Format transcript
    transcript_text = "\n\n".join(
        f"User: {t.user}\n\nAgent: {t.agent}"
        + (f"\n\n[Tool calls: {t.tool_calls}]" if t.tool_calls else "")
        for t in transcript
    )

    judge_prompt = f"""## Rubric\n\n{rubric_text}\n\n## Transcript\n\n{transcript_text}"""

    # Create client if not provided
    client = anthropic_client or anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"]
    )

    response = client.messages.create(
        model=rubric_config.judge_model,
        max_tokens=512,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": judge_prompt}],
    )

    raw = response.content[0].text.strip()

    try:
        parsed = json.loads(raw)
        return JudgeVerdict(
            score=float(parsed["score"]),
            feedback=str(parsed["feedback"]),
            raw_response=raw,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # Fallback: extract score heuristically
        import re
        score_match = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        score = float(score_match.group(1)) if score_match else 0.0
        return JudgeVerdict(score=score, feedback=f"Parse error: {e}. Raw: {raw}", raw_response=raw)
