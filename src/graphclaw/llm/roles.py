# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm.roles — Closed set of LLM-calling roles for model routing.

Description
-----------
Every place in GraphClaw that calls an LLM does a distinct *kind* of job:
the orchestrator routes and selects tools, sub-agents generate deliverables,
skill workers run single-shot completions, and the distiller/classifier/
summarizer do cheap structured extraction. Historically all of these shared
one process-wide client and one model. ``LLMRole`` names each job so a
:class:`~graphclaw.llm.routing.ModelRouter` can resolve each to an
independently configured provider+model.

Design Patterns
---------------
- Enum as closed vocabulary: new roles require a deliberate code change,
  not an arbitrary string appearing in an env var.

Public API
----------
- LLMRole: the six roles.
- DEFAULT_ROLE: role used when none is specified.
"""

from __future__ import annotations

from enum import StrEnum


class LLMRole(StrEnum):
    """A distinct LLM-calling job with its own model routing policy."""

    #: MainOrchestrator — routing / tool selection, needs strong tool-use fidelity.
    ORCHESTRATOR = "orchestrator"
    #: SubAgentRunner — generation (emails, reports), needs strong tool-use fidelity.
    SUBAGENT = "subagent"
    #: SkillWorker — single-shot completion, no tool use.
    SKILL = "skill"
    #: Post-turn distillation — cheap structured extraction.
    DISTILL = "distill"
    #: Inbound classification / profile synthesis — cheap structured extraction.
    CLASSIFY = "classify"
    #: Rolling-history / compaction summarization — cheap prose summarization.
    SUMMARIZE = "summarize"


DEFAULT_ROLE: LLMRole = LLMRole.ORCHESTRATOR
