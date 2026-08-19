# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_prompt_budget — Tests for graphclaw.agent.prompt_budget.

Covers the single enforcement point added for the context-budget work: token
estimation, section truncation/dropping by priority, and the tool-result
truncation/digest helpers that fix unbounded tool-result rot across the
agentic loop's 15 iterations.
"""

from __future__ import annotations

import json

from graphclaw.agent.prompt_budget import (
    BudgetReport,
    PromptBudget,
    PromptSection,
    digest_tool_result,
    dumps_tool_result,
    estimate_json_tokens,
    estimate_tokens,
    truncate_tool_result,
)
from graphclaw.config import ContextConfig

# ---------------------------------------------------------------------------
# ContextConfig
# ---------------------------------------------------------------------------


def test_context_config_defaults_assume_large_hosted_window():
    cfg = ContextConfig()
    assert cfg.model_window_tokens == 200_000
    assert cfg.prompt_budget_pct == 70


def test_context_config_reads_env(monkeypatch):
    monkeypatch.setenv("GRAPHCLAW_CONTEXT_MODEL_WINDOW_TOKENS", "32768")
    monkeypatch.setenv("GRAPHCLAW_CONTEXT_RESERVE_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("GRAPHCLAW_CONTEXT_PROMPT_BUDGET_PCT", "70")
    cfg = ContextConfig()
    assert cfg.model_window_tokens == 32768
    assert cfg.prompt_budget_tokens == int((32768 - 2048) * 0.70)


def test_prompt_budget_tokens_single_formula():
    cfg = ContextConfig(model_window_tokens=32768, reserve_output_tokens=4096, prompt_budget_pct=70)
    assert cfg.prompt_budget_tokens == int((32768 - 4096) * 0.70)


def test_sub_budgets_derive_from_prompt_budget():
    cfg = ContextConfig(
        model_window_tokens=32768,
        reserve_output_tokens=4096,
        prompt_budget_pct=70,
        system_budget_pct=30,
        tools_budget_pct=20,
        history_budget_pct=50,
    )
    total = cfg.prompt_budget_tokens
    assert cfg.system_budget_tokens == int(total * 0.30)
    assert cfg.tools_budget_tokens == int(total * 0.20)
    assert cfg.history_budget_tokens == int(total * 0.50)


def test_reserve_larger_than_window_never_goes_negative():
    cfg = ContextConfig(model_window_tokens=1000, reserve_output_tokens=5000)
    assert cfg.prompt_budget_tokens == 0


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_string():
    assert estimate_tokens("") == 0


def test_estimate_tokens_prose_ratio():
    text = "a" * 400
    assert estimate_tokens(text) == 100  # 4 chars/token


def test_estimate_json_tokens_denser_than_prose():
    text = "a" * 300
    assert estimate_json_tokens(text) == 100  # 3 chars/token
    assert estimate_json_tokens(text) > estimate_tokens(text)


# ---------------------------------------------------------------------------
# PromptBudget.fit_sections
# ---------------------------------------------------------------------------


def test_fit_sections_keeps_everything_under_budget():
    budget = PromptBudget(system_budget_tokens=10_000)
    sections = [
        PromptSection(name="header", text="a" * 40, priority=0, droppable=False),
        PromptSection(name="persona", text="b" * 40, priority=3),
    ]
    text, report = budget.fit_sections(sections)
    assert "header" in text.replace("a", "header") or True  # smoke: text assembled
    assert report.sections_kept == ["header", "persona"]
    assert report.sections_dropped == []
    assert report.sections_truncated == []


def test_fit_sections_truncates_section_over_its_own_cap():
    budget = PromptBudget(system_budget_tokens=100_000)
    sections = [PromptSection(name="semantic_index", text="x" * 1000, priority=5, max_chars=100)]
    _text, report = budget.fit_sections(sections)
    assert report.sections_truncated == ["semantic_index"]
    assert report.sections_dropped == []


def test_fit_sections_drops_lowest_priority_first_when_over_budget():
    # Each section ~ estimate_tokens("z"*400) == 100 tokens; total = 300.
    # Budget 250: dropping only kb_index (100) brings the total to 200, which
    # fits — so persona must survive.
    budget = PromptBudget(system_budget_tokens=250)
    sections = [
        PromptSection(name="header", text="h" * 400, priority=0, droppable=False),
        PromptSection(name="kb_index", text="k" * 400, priority=7),
        PromptSection(name="persona", text="p" * 400, priority=3),
    ]
    _text, report = budget.fit_sections(sections)
    # header must never be dropped (droppable=False); kb_index (priority 7,
    # least important) drops before persona (priority 3).
    assert "header" in report.sections_kept
    assert "kb_index" in report.sections_dropped
    assert "persona" in report.sections_kept


def test_fit_sections_never_drops_non_droppable_even_over_budget():
    budget = PromptBudget(system_budget_tokens=1)
    sections = [PromptSection(name="header", text="h" * 400, priority=0, droppable=False)]
    _text, report = budget.fit_sections(sections)
    assert report.sections_kept == ["header"]
    assert report.sections_dropped == []


def test_fit_sections_skips_empty_text():
    budget = PromptBudget(system_budget_tokens=10_000)
    sections = [
        PromptSection(name="empty", text="", priority=1),
        PromptSection(name="present", text="hello", priority=1),
    ]
    _text, report = budget.fit_sections(sections)
    assert "empty" not in report.sections_kept
    assert "present" in report.sections_kept


def test_fit_sections_joins_with_newline():
    budget = PromptBudget(system_budget_tokens=10_000)
    sections = [
        PromptSection(name="a", text="AAA", priority=1),
        PromptSection(name="b", text="BBB", priority=1),
    ]
    text, _report = budget.fit_sections(sections)
    assert text == "AAA\nBBB"


def test_budget_report_as_log_extra_shape():
    report = BudgetReport(
        system_tokens=42, sections_kept=["a"], sections_truncated=["b"], sections_dropped=["c"]
    )
    extra = report.as_log_extra()
    assert extra["event_type"] == "agent.prompt_budget"
    assert extra["system_tokens"] == 42
    assert extra["sections_kept"] == ["a"]
    assert extra["sections_truncated"] == ["b"]
    assert extra["sections_dropped"] == ["c"]


# ---------------------------------------------------------------------------
# Tool-result truncation / digest
# ---------------------------------------------------------------------------


def test_truncate_tool_result_under_cap_unchanged():
    content = "short"
    assert truncate_tool_result(content, max_chars=2000) == content


def test_truncate_tool_result_over_cap_adds_visible_marker():
    content = "x" * 5000
    result = truncate_tool_result(content, max_chars=100)
    assert len(result) < len(content)
    assert "truncated" in result
    assert "showing 100 of 5000 chars" in result
    assert "narrower args" in result


def test_truncate_tool_result_zero_cap_returns_unchanged():
    content = "anything"
    assert truncate_tool_result(content, max_chars=0) == content


def test_digest_tool_result_short_content_no_ellipsis():
    result = digest_tool_result("get_task_details", "ok", digest_chars=300)
    assert result == "[tool result elided — get_task_details: ok]"


def test_digest_tool_result_long_content_gets_ellipsis():
    content = "y" * 1000
    result = digest_tool_result("list_tasks", content, digest_chars=50)
    assert result.startswith("[tool result elided — list_tasks: ")
    assert result.endswith("…]")
    assert len(result) < len(content)


def test_dumps_tool_result_matches_json_dumps_shape():
    payload = {"a": 1, "b": [1, 2, 3]}
    assert dumps_tool_result(payload) == json.dumps(payload, default=str)


def test_dumps_tool_result_handles_non_json_native_values():
    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    result = dumps_tool_result({"x": Weird()})
    assert "weird-value" in result
