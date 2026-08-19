# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.prompt_budget — Single enforcement point for prompt token budgets.

Description
-----------
Before this module, the orchestrator's system prompt had three unbounded
sections (persona, semantic-memory index, agent catalog), tool results were
appended to the LLM conversation with no cap and never pruned across the
15-iteration agentic loop, and the one budget check that existed
(``ContextManager._enforce_budget``) measured only the compressed history —
excluding the system prompt and tool schemas entirely. On a large hosted
context window (200k tokens) none of this mattered. On a local 32k-token
model it means the very first turn can already exceed the window.

``PromptBudget`` gives every caller ONE formula for "how many tokens does
this cost" and ONE place that decides what to truncate or drop when a
turn's assembled context would exceed the configured window. Callers build
a list of :class:`PromptSection` (or use :func:`truncate_tool_result` /
:func:`digest_tool_result` for tool-result rot) and get back the fitted
text plus a :class:`BudgetReport` for structured logging
(``agent.prompt_budget`` — the primary verification instrument for the
context-budget work; see docs/planning/build-plan.md, Wave Model-Routing).

Design Patterns
---------------
- Strategy-free by design: this module has NO knowledge of what a "persona"
  or "graph summary" section is. Callers (``MainOrchestrator``,
  ``SubAgentRunner``) supply already-rendered text; this module only
  measures and fits it. That keeps prompt-budget logic reusable for both
  the orchestrator's system prompt and a sub-agent's system prompt.
- Priority-ordered eviction: sections are dropped by ascending
  ``droppable``-then-descending-``priority`` order — priority 0 sections
  (e.g. the response-format instruction, onboarding) are never dropped.

Public API
----------
- PromptSection: one named, priority-ranked, optionally capped text block.
- BudgetReport: what was kept/truncated/dropped, for structured logging.
- PromptBudget: fits a list of PromptSection into a token budget.
- estimate_tokens: cheap char-based token estimate for prose.
- estimate_json_tokens: cheap char-based token estimate for dense JSON
  (tool results), which run closer to 2.5-3 chars/token than prose's ~4.
- estimate_tool_tokens: cheap estimate of a ToolDefinition list's JSON cost.
- truncate_tool_result: cap a tool result's rendered JSON with a visible
  truncation marker the model can act on (re-call with narrower args).
- digest_tool_result: collapse an older tool result to a short one-line
  digest, used when pruning across agentic-loop iterations.

Dependencies
------------
- graphclaw.config: ContextConfig (budget knobs).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Prose (system-prompt sections, conversation text) averages ~4 chars/token
# for English. Dense JSON (tool results) runs denser — closer to 2.5-3
# chars/token — because punctuation-heavy structure and short keys don't
# compress the way prose does. Using the prose ratio for JSON would
# systematically under-count exactly the payload causing the problem.
_PROSE_CHARS_PER_TOKEN = 4
_JSON_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """Cheap character-based token estimate for prose text.

    Deliberately not a real tokenizer call: ``LLMClient.count_tokens`` is a
    network round trip for LiteLLM/Anthropic backends, and prompt assembly
    calls this once per candidate section — a real call per section would
    turn one turn into a dozen extra network round trips. Use this for
    shaping; reserve ``count_tokens`` for one optional pre-flight assertion
    behind ``LLM_TRACE``.
    """
    if not text:
        return 0
    return len(text) // _PROSE_CHARS_PER_TOKEN


def estimate_json_tokens(text: str) -> int:
    """Cheap character-based token estimate for dense JSON (tool results)."""
    if not text:
        return 0
    return len(text) // _JSON_CHARS_PER_TOKEN


def estimate_tool_tokens(tools: list[Any]) -> int:
    """Cheap estimate of the JSON-schema cost of a list of ``ToolDefinition``.

    Used for the one-time "fixed overhead" figure fed into
    ``ContextManager._enforce_budget`` — the schema payload doesn't change
    within a single budget check, so this only needs to run once per turn,
    not per section.
    """
    if not tools:
        return 0
    rendered = json.dumps(
        [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools],
        default=str,
    )
    return estimate_json_tokens(rendered)


@dataclass(frozen=True)
class PromptSection:
    """One named, priority-ranked, optionally capped block of prompt text.

    Attributes:
        name: Identifier used in BudgetReport (e.g. ``"persona"``,
            ``"graph_summary"``). Not shown to the model.
        text: The already-rendered section text (including any markdown
            header the caller wants included).
        priority: Eviction order when the budget is exceeded — 0 is dropped
            last (in practice: never, if the caller reserves 0 for
            must-keep sections like onboarding or the response-format
            instruction). Higher values are dropped first.
        max_chars: Per-section hard cap applied before priority-based
            dropping. ``None`` means no section-specific cap (only the
            overall budget applies).
        droppable: If ``False``, this section is truncated but never fully
            dropped regardless of budget pressure — use sparingly, for
            sections whose complete absence would break the prompt's
            structure (e.g. the header).
    """

    name: str
    text: str
    priority: int = 5
    max_chars: int | None = None
    droppable: bool = True


@dataclass
class BudgetReport:
    """What PromptBudget.fit_sections actually did — for structured logging."""

    system_tokens: int
    sections_kept: list[str] = field(default_factory=list)
    sections_truncated: list[str] = field(default_factory=list)
    sections_dropped: list[str] = field(default_factory=list)

    def as_log_extra(self) -> dict[str, Any]:
        """Shape matching the existing ``extra={...}`` structured-logging style."""
        return {
            "event_type": "agent.prompt_budget",
            "system_tokens": self.system_tokens,
            "sections_kept": self.sections_kept,
            "sections_truncated": self.sections_truncated,
            "sections_dropped": self.sections_dropped,
        }


class PromptBudget:
    """Fits a list of :class:`PromptSection` into a token budget.

    Args:
        system_budget_tokens: Total token budget available for the fitted
            sections (typically ``ContextConfig.system_budget_tokens`` for
            the orchestrator, or a sub-agent-specific cap).
    """

    def __init__(self, system_budget_tokens: int) -> None:
        self._budget_tokens = max(0, system_budget_tokens)

    def fit_sections(self, sections: list[PromptSection]) -> tuple[str, BudgetReport]:
        """Truncate over-cap sections, then drop lowest-priority-first until
        the total fits the budget. Returns the joined text and a report.

        Truncation happens before dropping: a section with ``max_chars`` set
        is capped to that length regardless of overall budget pressure, so
        one runaway section (e.g. an unbounded semantic-memory index) cannot
        by itself starve every other section of its fair share.
        """
        working: list[PromptSection] = []
        truncated_names: list[str] = []
        for section in sections:
            if not section.text:
                continue
            if section.max_chars is not None and len(section.text) > section.max_chars:
                capped_text = section.text[: section.max_chars].rstrip()
                capped_text += f"\n…(truncated, {len(section.text)} chars total)"
                working.append(
                    PromptSection(
                        name=section.name,
                        text=capped_text,
                        priority=section.priority,
                        max_chars=section.max_chars,
                        droppable=section.droppable,
                    )
                )
                truncated_names.append(section.name)
            else:
                working.append(section)

        kept = list(working)
        dropped_names: list[str] = []

        def _total_tokens(items: list[PromptSection]) -> int:
            return sum(estimate_tokens(s.text) for s in items)

        # Drop droppable sections, highest priority-number (lowest importance)
        # first, until we fit the budget or run out of droppable sections.
        droppable_sorted = sorted(
            (s for s in kept if s.droppable), key=lambda s: s.priority, reverse=True
        )
        idx = 0
        while _total_tokens(kept) > self._budget_tokens and idx < len(droppable_sorted):
            victim = droppable_sorted[idx]
            idx += 1
            if victim in kept:
                kept.remove(victim)
                dropped_names.append(victim.name)

        text = "\n".join(s.text for s in kept)
        report = BudgetReport(
            system_tokens=_total_tokens(kept),
            sections_kept=[s.name for s in kept],
            sections_truncated=truncated_names,
            sections_dropped=dropped_names,
        )
        if dropped_names:
            logger.info("agent.prompt_budget", extra=report.as_log_extra())
        return text, report


def truncate_tool_result(content: str, max_chars: int) -> str:
    """Cap a rendered tool-result string with a marker the model can act on.

    A silently clipped JSON blob teaches the model that data is missing; an
    explicit marker teaches it to re-call with narrower arguments instead of
    assuming the tool failed. Small models are especially sensitive to this
    distinction — see docs/planning/build-plan.md, Wave Model-Routing.
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    marker = f"\n…[truncated: showing {max_chars} of {len(content)} chars. Re-call with narrower args if you need more.]"
    # Keep the marker itself inside max_chars-ish territory rather than
    # appending unconditionally past the cap.
    return content[:max_chars].rstrip() + marker


def digest_tool_result(tool_name: str, content: str, digest_chars: int) -> str:
    """Collapse an older tool result to a short digest for cross-iteration pruning.

    Used when a tool-result message is old enough (beyond
    ``tool_result_keep_recent``) that its full content is no longer needed,
    but the message itself must be kept (never delete a ``role="tool"``
    message — providers reject an orphaned ``tool_use``/``tool_result``
    pairing).
    """
    snippet = content[:digest_chars].rstrip()
    if len(content) > digest_chars:
        snippet += "…"
    return f"[tool result elided — {tool_name}: {snippet}]"


def dumps_tool_result(result: Any) -> str:
    """``json.dumps`` a tool result the same way call sites already do.

    Centralised so truncation/digest helpers and call sites agree on the
    exact string being measured and truncated.
    """
    return json.dumps(result, default=str)
