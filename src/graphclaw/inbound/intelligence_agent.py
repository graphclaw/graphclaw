"""graphclaw.inbound.intelligence_agent — LLM-powered task intelligence extraction.

Description
-----------
``InboundIntelligenceAgent`` processes inbound messages to extract two types of
intelligence: (1) task-specific log entries appended to a node's intelligence
field, and (2) general behavioral observations stored in the agent's working
memory. Uses a lightweight LLM (default: claude-haiku-4-5) for single-pass
extraction with structured JSON output. All PII is scrubbed before storage.

Design Patterns
---------------
- Facade: Wraps the LLM call, PII scrubbing, graph update, and memory write in a
  single async ``process()`` method; callers don't coordinate sub-components.
- Dependency Injection: LLM client, graph repo, storage client, and memory lock
  are all injected at construction, enabling clean testing and flexible deployment.
- PII-Safe by Default: All extracted text passes through ``_scrub_pii()`` before
  being written to the graph or object storage, preventing accidental leakage of
  SSNs, credit card numbers, and phone numbers.
- Graceful Degradation: If the LLM returns invalid JSON, both fields are set to
  None and the error is logged; the pipeline continues without crashing.

Public API
----------
- IntelligenceUpdate: Result model with task_intelligence, memory_update, and action_taken.
- InboundIntelligenceAgent: Main processor class.
- InboundIntelligenceAgent.process: Extract intelligence from an inbound message.
- INTELLIGENCE_AGENT_MODEL_ENV: Env var name for model override.
- DEFAULT_INTELLIGENCE_MODEL: Default model (claude-haiku-4-5).
- MAX_INTELLIGENCE_WORDS: Word count limit for node intelligence field (500).

Dependencies
------------
- asyncio: Lock for memory file synchronization.
- datetime: Timestamp for intelligence log entries.
- json: Parse LLM JSON responses.
- os: Read environment variables for model selection.
- re: PII pattern matching and scrubbing.
- pydantic: BaseModel for IntelligenceUpdate.
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.llm.base: LLMClient, LLMMessage.
- graphclaw.infra.logger: AsyncLogger.
- graphclaw.infra.storage: StorageClient, StoragePaths.

Notes
-----
The LLM system prompt enforces a strict 60-word limit on task entries and
requires one-line memory notes to keep intelligence concise and scannable.
Intelligence entries are prepended (newest first) and auto-trimmed if they
exceed MAX_INTELLIGENCE_WORDS. Memory notes are appended under the
"## Recent Context" heading in working/context.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from graphclaw.llm.base import LLMClient, LLMMessage

if TYPE_CHECKING:
    from graphclaw.gateway.schemas import InboundMessage
    from graphclaw.infra.logger import AsyncLogger
    from graphclaw.infra.storage import StorageClient

from graphclaw.infra.storage import StoragePaths

__all__ = [
    "IntelligenceUpdate",
    "InboundIntelligenceAgent",
    "INTELLIGENCE_AGENT_MODEL_ENV",
    "DEFAULT_INTELLIGENCE_MODEL",
    "MAX_INTELLIGENCE_WORDS",
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

INTELLIGENCE_AGENT_MODEL_ENV = "INTELLIGENCE_AGENT_MODEL"
DEFAULT_INTELLIGENCE_MODEL = "claude-haiku-4-5"
MAX_INTELLIGENCE_WORDS = 500


# ---------------------------------------------------------------------------
# PII scrubbing patterns
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b"), "[REDACTED-CC]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED-PHONE]"),
]


def _scrub_pii(text: str) -> str:
    """Remove common PII patterns from text before storing in intelligence fields.

    Args:
        text: Input string potentially containing PII.

    Returns:
        Scrubbed text with PII replaced by redaction markers.
    """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class IntelligenceUpdate(BaseModel):
    """Result of processing an inbound message for intelligence extraction.

    Attributes
    ----------
    task_intelligence:
        Text to append to the node's intelligence field. ``None`` if the
        message had no task-specific content worth logging.
    memory_update:
        Text to append to the agent's working/context.md memory file.
        ``None`` if no general observation was extracted.
    action_taken:
        One of ``"node_updated"``, ``"memory_updated"``, ``"both"``,
        ``"unmatched"``, or ``"error"``.
    """

    task_intelligence: str | None = None
    memory_update: str | None = None
    action_taken: str = "unmatched"


# ---------------------------------------------------------------------------
# Intelligence Agent
# ---------------------------------------------------------------------------


class InboundIntelligenceAgent:
    """LLM-powered processor for extracting task intelligence and memory notes.

    Uses a lightweight LLM (default: claude-haiku-4-5) to parse inbound messages
    and generate (1) a concise task-specific log entry for the resolved node's
    intelligence field, and (2) a general observation for the agent's working
    memory. All extracted text is scrubbed for PII before storage.

    Args:
        llm:
            LLMClient instance (any provider) for the extraction call.
        graph_repo:
            Graph repository with ``get_node_intelligence`` and
            ``update_node_intelligence`` methods. Typed as ``Any`` to avoid
            circular import.
        storage:
            StorageClient for reading/writing agent memory files.
        memory_lock:
            Async lock shared across all memory writers to prevent
            read-modify-write races on context.md.
        logger:
            Optional AsyncLogger for structured logging. If ``None``, logging
            is skipped.
    """

    def __init__(
        self,
        llm: LLMClient,
        graph_repo: Any,
        storage: StorageClient,
        memory_lock: asyncio.Lock,
        logger: AsyncLogger | None = None,
    ) -> None:
        self._llm = llm
        self._graph_repo = graph_repo
        self._storage = storage
        self._memory_lock = memory_lock
        self._logger = logger
        self._model = os.getenv(INTELLIGENCE_AGENT_MODEL_ENV, DEFAULT_INTELLIGENCE_MODEL)

    async def process(
        self,
        inbound: InboundMessage,
        resolution: Any,  # InboundResult from InboundProcessor
        agent_id: str,
        user_id: str,
    ) -> IntelligenceUpdate:
        """Extract intelligence from an inbound message and update graph + memory.

        Workflow:
        1. Read existing node intelligence (if task_id is available).
        2. Make a single LLM call to extract task_entry and memory_note as JSON.
        3. Parse the LLM response, handling JSON errors gracefully.
        4. Scrub PII from both extracted strings.
        5. Update node intelligence (prepend log line, trim if over word limit).
        6. Update agent working memory (append under "## Recent Context").
        7. Log the event if logger is available.
        8. Return IntelligenceUpdate with action_taken summary.

        Args:
            inbound:
                The normalized inbound message.
            resolution:
                The InboundResult from the resolution pipeline; if
                ``resolution.resolution.task_id`` is set, we update that node.
            agent_id:
                Agent identifier for memory path construction.
            user_id:
                User identifier for memory path construction and logging.

        Returns:
            IntelligenceUpdate with task_intelligence, memory_update, and action_taken.
        """
        # 1. Read existing intelligence if task_id is available
        task_id = None
        existing_intelligence = None
        if resolution.resolution and resolution.resolution.task_id:
            task_id = resolution.resolution.task_id
            existing_intelligence = await self._graph_repo.get_node_intelligence(task_id)

        # 2. Build LLM prompt
        system_prompt = (
            "You are a task intelligence processor for a task management system.\n"
            'Given an inbound message, produce exactly two outputs as valid JSON with keys "task_entry" and "memory_note":\n'
            '- "task_entry": A single-line intelligence log entry (max 60 words) describing what was communicated, '
            'in format "[{channel} | inbound | {concise factual summary}]". Set to null if the message has no clear task-specific content.\n'
            '- "memory_note": A one-line general observation about user communication preferences, behavioral patterns, '
            "or project-level context for the agent's working memory. Set to null if nothing general to record.\n"
            "Never include raw PII such as SSNs, credit card numbers, financial account numbers, or medical information in either field. "
            "Summarize rather than copy verbatim.\n"
            "Respond with ONLY valid JSON, no markdown fences."
        )

        user_content = (
            f"Channel: {inbound.channel}\n"
            f"From: {inbound.sender}\n"
            f"Subject: {inbound.subject or '(no subject)'}\n"
            f"Body: {inbound.body[:600] if inbound.body else '(empty)'}\n"
            f"Matched task ID: {task_id or 'none'}\n"
            f"Existing task intelligence (last 200 chars): {existing_intelligence[-200:] if existing_intelligence else 'none'}"
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_content),
        ]

        # 3. Make LLM call and parse response
        task_entry: str | None = None
        memory_note: str | None = None
        parse_error = False

        try:
            response = await self._llm.complete(
                messages,
                model=self._model,
                max_tokens=512,
                temperature=0.0,
            )
            # Parse JSON from response content
            extracted = json.loads(response.content.strip())
            task_entry = extracted.get("task_entry")
            memory_note = extracted.get("memory_note")
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            parse_error = True
            if self._logger:
                await self._logger.log(
                    "ERROR",
                    "agent.intelligence_parse_error",
                    session_id=inbound.session_id,
                    task_id=task_id,
                    error=str(exc),
                )

        # 4. Scrub PII from extracted strings
        if task_entry:
            task_entry = _scrub_pii(task_entry)
        if memory_note:
            memory_note = _scrub_pii(memory_note)

        # 5. Update node intelligence if task_entry is set and task_id is available
        node_updated = False
        if task_entry and task_id:
            # Build the full log line
            date_str = datetime.now(timezone.utc).date().isoformat()
            # Strip any existing brackets from task_entry since we add them
            clean_entry = task_entry.strip("[]")
            log_line = f"[{date_str}] {inbound.channel} | inbound | {clean_entry}"

            # Prepend to existing intelligence (newest first)
            new_text = log_line + "\n" + (existing_intelligence or "")

            # Trim if over word limit
            words = new_text.split()
            if len(words) > MAX_INTELLIGENCE_WORDS:
                # Keep first MAX_INTELLIGENCE_WORDS words
                trimmed_words = words[:MAX_INTELLIGENCE_WORDS]
                trimmed_text = " ".join(trimmed_words)
                # Count how many lines we dropped
                dropped_count = len(words) - MAX_INTELLIGENCE_WORDS
                trimmed_text += f"\n... {dropped_count} older entries archived"
            else:
                trimmed_text = new_text

            await self._graph_repo.update_node_intelligence(task_id, trimmed_text)
            node_updated = True

        # 6. Update agent working memory if memory_note is set
        memory_updated = False
        if memory_note:
            async with self._memory_lock:
                path = StoragePaths.agent_memory_working(user_id, agent_id)
                try:
                    existing_context = await self._storage.read(path)
                    context_text = existing_context.decode("utf-8")
                except Exception:
                    # File doesn't exist or read failed; start fresh
                    context_text = "# Working Context\n"

                # Append note under "## Recent Context" heading
                if "## Recent Context" in context_text:
                    # Find the heading and append on the next line
                    lines = context_text.split("\n")
                    inserted = False
                    for i, line in enumerate(lines):
                        if line.strip() == "## Recent Context":
                            # Insert after this line (or after any existing first line under it)
                            if i + 1 < len(lines):
                                lines.insert(i + 1, memory_note)
                            else:
                                lines.append(memory_note)
                            inserted = True
                            break
                    if inserted:
                        context_text = "\n".join(lines)
                    else:
                        # Fallback: append at end
                        context_text += f"\n\n## Recent Context\n{memory_note}"
                else:
                    # No "## Recent Context" heading; append at end
                    context_text += f"\n\n## Recent Context\n{memory_note}"

                await self._storage.write(path, context_text.encode("utf-8"))
                memory_updated = True

        # 7. Determine action_taken
        if parse_error:
            action_taken = "error"
        elif node_updated and memory_updated:
            action_taken = "both"
        elif node_updated:
            action_taken = "node_updated"
        elif memory_updated:
            action_taken = "memory_updated"
        else:
            action_taken = "unmatched"

        # 8. Log the event
        if self._logger:
            await self._logger.log(
                "INFO",
                "agent.intelligence_update",
                session_id=inbound.session_id,
                task_id=task_id,
                channel=inbound.channel,
                direction="inbound",
                action_taken=action_taken,
            )

        # 9. Return result
        return IntelligenceUpdate(
            task_intelligence=task_entry if node_updated else None,
            memory_update=memory_note if memory_updated else None,
            action_taken=action_taken,
        )
