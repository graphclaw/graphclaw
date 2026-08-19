# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.context — ContextManager: conversation history compression.

Description
-----------
Provides ``ContextManager``, a five-stage pipeline that compresses conversation
history before it is sent to the LLM, keeping token cost bounded as conversations
grow long.

Compression Pipeline (applied in order)
----------------------------------------
0. Fast path — when ``len(history) <= window_size`` there is nothing to compress;
   return the verbatim history immediately, skipping the summary and token-budget
   API calls.
1. Session entity extraction — scan history for node IDs (TSK-*, GOAL-*, MCP-*)
   and build a compact ``## Session State`` register.  Always included.
2. Sliding window — keep the last ``window_size`` (default 20) turns verbatim.
3. Tool-call collapse — for assistant+tool+result triples outside the window,
   emit a single summary line.  Failed tool calls are preserved with a longer
   excerpt and a ``[FAILED tool action: ...]`` prefix so the agent avoids repeats.
4. Rolling LLM summary — when history older than the window exceeds
   ``summary_threshold`` turns, make a cheap Haiku call to summarise them into
   structured Goals/Progress/Blocking/Entities sections.
5. Token budget check — use ``LLMClient.count_tokens()`` before the main call.
   If over ``budget_tokens`` (default 80 000), tighten ``window_size`` and retry.

History Format
--------------
``chat.py`` stores history entries as ``{"role": "user"|"agent", "content": str}``.
``ContextManager`` remaps ``"agent"`` → ``"assistant"`` when constructing
``LLMMessage`` objects.

Public API
----------
- CompressedContext: Dataclass holding the compression result.
- ContextManager: Compresses history for one session.
- ContextManager.compress: Main entry point — returns a CompressedContext.
- ContextManager.build_messages: Convert a CompressedContext to LLMMessage list.

Dependencies
------------
- graphclaw.llm.base: LLMClient, LLMMessage, ToolDefinition.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from graphclaw.llm.base import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# Regex to extract node IDs from message content
_NODE_ID_RE = re.compile(r"\b(TSK-[A-Z0-9\-]+|GOAL-[A-Z0-9\-]+|MCP-[A-Z0-9\-]+)\b")
_STATE_RE = re.compile(
    r"\b(PENDING|ACTIVE|IN_PROGRESS|BLOCKED|DELAYED|NEEDS_REVIEW|COMPLETE|CANCELLED|SNOOZED)\b"
)


@dataclass
class CompressedContext:
    """Result of the compression pipeline.

    Attributes
    ----------
    session_state_block:
        Compact entity register (node IDs + states seen in history).
        Always included — gives the LLM a persistent entity register.
    summary_block:
        Rolling LLM-generated summary of old turns (may be empty string).
    recent_messages:
        The last ``window_size`` turns verbatim as LLMMessage objects.
    collapsed_tool_calls:
        One-line summaries of tool calls outside the window.
    compression_applied:
        True if any stage beyond the sliding window was engaged.
    original_count:
        Number of history entries before compression.
    compressed_count:
        Number of LLMMessage objects in the final output.
    """

    session_state_block: str = ""
    summary_block: str = ""
    recent_messages: list[LLMMessage] = field(default_factory=list)
    collapsed_tool_calls: str = ""
    compression_applied: bool = False
    original_count: int = 0
    compressed_count: int = 0


class ContextManager:
    """Compresses conversation history for one agent session.

    Parameters
    ----------
    llm_client:
        LLM client used for the rolling summary sub-call (Haiku, no tools).
    window_size:
        Number of most-recent turns to keep verbatim (default 20).
    summary_threshold:
        When turns outside the window exceed this count, trigger LLM summary
        (default 30).
    budget_tokens:
        Target token budget — if exceeded, tighten window and retry
        (default 80 000).
    """

    # Bound on distinct users' rolling summaries kept in memory at once — an
    # LRU-ish cap so a long-running process doesn't accumulate one entry per
    # user forever. Eviction is simplest-possible (oldest insertion order via
    # dict), not true LRU; that's enough for a leak guard.
    _MAX_ROLLING_SUMMARY_USERS = 200

    def __init__(
        self,
        llm_client: LLMClient,
        window_size: int = 20,
        summary_threshold: int = 30,
        budget_tokens: int = 80_000,
    ) -> None:
        self._llm = llm_client
        self._window_size = window_size
        self._summary_threshold = summary_threshold
        self._budget_tokens = budget_tokens
        # Rolling summary text, keyed by user_id. ContextManager is owned by
        # a process-wide singleton MainOrchestrator (one instance for every
        # user), so a single shared self._rolling_summary string used to mean
        # user A's summarised conversation could be injected into user B's
        # prompt whenever B's turn hit the `elif self._rolling_summary:`
        # branch in compress(). Keying by user_id closes that cross-user leak.
        self._rolling_summaries: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compress(
        self,
        history: list[dict],
        current_messages: list[LLMMessage] | None = None,
        *,
        user_id: str = "",
        fixed_overhead_tokens: int = 0,
    ) -> CompressedContext:
        """Run the compression pipeline and return a ``CompressedContext``.

        Parameters
        ----------
        history:
            Raw history entries from ``chat.py``: ``{"role": "user"|"agent", "content": str}``.
        current_messages:
            The current turn's messages (user message + any in-flight context).
            Used only for the token budget check.
        user_id:
            Whose rolling summary to read/update. Required to keep the
            summary from leaking across users on this shared, process-wide
            ContextManager — see ``self._rolling_summaries``. Defaults to
            ``""`` (its own bucket) only for backward compatibility with
            direct/test callers that don't have a user_id; production call
            sites must always pass one.
        fixed_overhead_tokens:
            Estimated tokens already spent on the system prompt and active
            tool schemas for this turn — added to the compressed-history
            estimate in ``_enforce_budget`` so the budget check reflects what
            actually gets sent, not just the history slice. Previously this
            check ignored the system prompt and tool schemas entirely (often
            5-13k tokens), so a turn could ship well over budget while the
            check reported success. Callers should pass
            ``estimate_tokens(system_prompt) + estimate_tool_tokens(active_tools)``.
        """
        result = CompressedContext(original_count=len(history))

        if not history:
            return result

        # Fast path — when the whole history fits in the verbatim window there is
        # nothing to compress: skip the rolling summary (Haiku call) and the token
        # budget check (count_tokens call), saving ~200-500ms per short turn.
        if len(history) <= self._window_size:
            result.session_state_block = self._extract_entity_register(history)
            result.recent_messages = self._to_llm_messages(history)
            result.compressed_count = len(result.recent_messages)
            return result

        # Step 1 — Session entity extraction
        result.session_state_block = self._extract_entity_register(history)

        # Step 2 — Sliding window
        window = history[-self._window_size :]
        older = history[: -self._window_size] if len(history) > self._window_size else []

        # Step 3 — Tool-call collapse (older turns)
        if older:
            result.collapsed_tool_calls = self._collapse_tool_calls(older)
            result.compression_applied = True

        # Step 4 — Rolling LLM summary (if older turns are numerous).
        # A non-empty summary REPLACES collapsed_tool_calls rather than
        # adding to it: the summary already covers the same older turns at
        # higher density, so keeping both means crossing summary_threshold
        # *increases* tokens instead of reducing them.
        if len(older) > self._summary_threshold:
            result.summary_block = await self._build_rolling_summary(older, user_id=user_id)
            if result.summary_block:
                result.collapsed_tool_calls = ""
            result.compression_applied = True
        elif self._rolling_summaries.get(user_id):
            result.summary_block = self._rolling_summaries[user_id]
            result.collapsed_tool_calls = ""

        # Convert window to LLMMessage objects
        result.recent_messages = self._to_llm_messages(window)
        result.compressed_count = len(result.recent_messages)

        # Step 5 — Token budget check (best-effort; don't fail if count_tokens unavailable)
        if current_messages is not None:
            await self._enforce_budget(
                result, history, current_messages, fixed_overhead_tokens=fixed_overhead_tokens
            )

        logger.debug(
            "context.compress",
            extra={
                "original": result.original_count,
                "window": len(window),
                "older": len(older),
                "compression_applied": result.compression_applied,
            },
        )
        return result

    def build_messages(self, ctx: CompressedContext) -> list[LLMMessage]:
        """Convert a ``CompressedContext`` into a flat list of ``LLMMessage`` objects.

        Ordering:
        1. Session state block (if present)
        2. Previous conversation summary (if present)
        3. Collapsed tool-call summaries (if present)
        4. Verbatim recent turns

        Sections 1-3 are concatenated into a single ``user``-role preamble
        message, not six separate messages with fake ``assistant`` replies
        ("Understood. I have the session state context.", etc.) — those
        synthetic turns cost ~150 tokens and, more importantly, are exactly
        the kind of fake-but-plausible turn a small model will imitate.
        """
        preamble_parts: list[str] = []

        if ctx.session_state_block:
            preamble_parts.append(f"[Context: {ctx.session_state_block}]")

        if ctx.summary_block:
            preamble_parts.append(f"[Previous conversation summary]\n{ctx.summary_block}")

        if ctx.collapsed_tool_calls:
            preamble_parts.append(f"[Earlier actions in this session]\n{ctx.collapsed_tool_calls}")

        messages: list[LLMMessage] = []
        if preamble_parts:
            messages.append(LLMMessage(role="user", content="\n\n".join(preamble_parts)))

        messages.extend(ctx.recent_messages)
        return messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_entity_register(self, history: list[dict]) -> str:
        """Scan history and build a compact entity register."""
        entities: dict[str, str] = {}  # node_id → latest state seen
        for entry in history:
            content = entry.get("content", "")
            for node_id in _NODE_ID_RE.findall(content):
                if node_id not in entities:
                    entities[node_id] = "mentioned"
            # Look for state transitions near node IDs
            for match in re.finditer(
                r"(TSK-[A-Z0-9\-]+)[^\n]*?(COMPLETE|CANCELLED|ACTIVE|BLOCKED|IN_PROGRESS)", content
            ):
                entities[match.group(1)] = match.group(2)

        if not entities:
            return ""

        lines = ["## Session State (nodes referenced in this conversation)"]
        for node_id, state in sorted(entities.items()):
            lines.append(f"- {node_id}: {state}")
        return "\n".join(lines)

    # Markers that indicate a tool call failed — failures are preserved with a
    # longer excerpt so the agent can see past errors and avoid repeating them.
    _FAILURE_MARKERS = ('"error"', "'error'", "failed", "[failed", "exception", "traceback")

    def _collapse_tool_calls(self, older: list[dict]) -> str:
        """Summarise older turns into 1-line tool-call entries.

        Failed tool calls are preserved with a longer excerpt (200 chars) and a
        ``[FAILED tool action: ...]`` prefix — the agent needs to see prior
        failures to avoid repeating them. Successful actions collapse to 120 chars.
        """
        lines: list[str] = []
        for entry in older:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if not content:
                continue
            if role in ("agent", "assistant"):
                content_lower = content.lower()
                is_tool = "[tool:" in content or "tool_call" in content_lower
                is_failure = any(m in content_lower for m in self._FAILURE_MARKERS)
                if is_failure:
                    lines.append(f"[FAILED tool action: {content[:200]}]")
                elif is_tool:
                    lines.append(f"[tool action: {content[:120]}]")
                else:
                    # Summarise as agent response
                    lines.append(f"[agent: {content[:100]}...]")
            else:
                lines.append(f"[user: {content[:100]}...]")
        return "\n".join(lines)

    async def _build_rolling_summary(self, older: list[dict], *, user_id: str = "") -> str:
        """Generate an LLM summary of the older conversation turns.

        FIX (Wave Model-Routing, Step 13 — separate flagged commit): this
        call previously passed ``system=...`` to ``LLMClient.complete``,
        which has no such parameter (see ``llm/base.py``). Every call raised
        ``TypeError``, was swallowed by an overly broad ``except Exception``,
        and silently returned the previous (usually empty) summary — rolling
        summarization has never actually worked. The fix prepends a
        ``role="system"`` message instead (every backend's ``complete``
        supports a leading system message). This turns on a previously-dead
        LLM call — real new latency/cost on the ``summarize`` role — which
        is exactly why it's isolated to its own commit rather than bundled
        with the accounting-only fixes around it.

        The summary is stored per ``user_id`` in ``self._rolling_summaries``
        rather than in one shared string — see the class docstring /
        ``__init__`` comment for why.
        """
        previous = self._rolling_summaries.get(user_id, "")
        if not older:
            return previous

        conversation_text = "\n".join(
            f"{e.get('role', 'user').upper()}: {e.get('content', '')[:300]}" for e in older
        )

        prompt = (
            "Summarise the following conversation excerpt using EXACTLY these four "
            "markdown sections, each as a short bullet list (omit a bullet if nothing "
            "applies, but keep all four headers):\n"
            "## Goals\nActive objectives the user is pursuing.\n"
            "## Progress\nTasks completed and decisions made.\n"
            "## Blocking\nOutstanding blockers and unanswered questions.\n"
            "## Entities\nKey node IDs (TSK-*, GOAL-*, MCP-*) and their current state.\n\n"
            "Be concise — this is a memory aid for downstream reasoning, not prose.\n\n"
            f"{conversation_text}"
        )

        try:
            summary_msgs = [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a structured conversation summariser. Output only the four "
                        "requested markdown sections (Goals/Progress/Blocking/Entities)."
                    ),
                ),
                LLMMessage(role="user", content=prompt),
            ]
            response = await self._llm.complete(
                messages=summary_msgs,
                max_tokens=512,
            )
            summary = response.content.strip()
            if len(self._rolling_summaries) >= self._MAX_ROLLING_SUMMARY_USERS:
                # Simplest-possible eviction: drop the oldest inserted entry.
                # Not true LRU, but bounds memory without extra bookkeeping.
                oldest_user = next(iter(self._rolling_summaries))
                if oldest_user != user_id:
                    del self._rolling_summaries[oldest_user]
            self._rolling_summaries[user_id] = summary
            return summary
        except (RuntimeError, TimeoutError, ValueError) as exc:
            # Narrowed from a bare `except Exception`, which is exactly how
            # the system= TypeError above went unnoticed for so long — a
            # broad catch here would silently swallow the next signature
            # drift too. logger.exception keeps the traceback for anything
            # that does hit this branch.
            logger.exception("context.summary_failed", extra={"error": str(exc)})
            return previous  # Fall back to this user's previous summary

    async def _enforce_budget(
        self,
        ctx: CompressedContext,
        history: list[dict],
        current_messages: list[LLMMessage],
        *,
        fixed_overhead_tokens: int = 0,
    ) -> None:
        """If token count exceeds budget, tighten the window and recompress.

        ``fixed_overhead_tokens`` (system prompt + active tool schemas) is
        added to every count in this method — the previous version measured
        only ``build_messages(ctx) + current_messages``, which could
        under-count the real prompt by 5-13k tokens and report "under
        budget" for a turn that was already over. It also shrank the window
        exactly once and never re-checked; this loops (bounded) until it
        actually fits or runs out of things to shrink, then drops the
        collapsed/summary blocks, then hard-truncates as a last resort.

        Uses ``estimate_tokens`` (char-based) for the loop, not
        ``count_tokens`` (a network call for LiteLLM/Anthropic) — shaping
        with an estimate and re-measuring on every candidate would turn one
        budget check into several round trips per turn.
        """
        from graphclaw.agent.prompt_budget import estimate_tokens  # noqa: PLC0415

        def _current_tokens() -> int:
            all_messages = self.build_messages(ctx) + current_messages
            return fixed_overhead_tokens + sum(estimate_tokens(m.content) for m in all_messages)

        try:
            if _current_tokens() <= self._budget_tokens:
                return

            logger.warning(
                "context.budget_exceeded",
                extra={"tokens": _current_tokens(), "budget": self._budget_tokens},
            )

            window_size = self._window_size
            for _ in range(4):
                window_size = max(3, int(window_size * 0.6))
                window = history[-window_size:]
                ctx.recent_messages = self._to_llm_messages(window)
                ctx.compression_applied = True
                if _current_tokens() <= self._budget_tokens or window_size <= 3:
                    break

            if _current_tokens() > self._budget_tokens and ctx.collapsed_tool_calls:
                ctx.collapsed_tool_calls = ""

            if _current_tokens() > self._budget_tokens and ctx.summary_block:
                ctx.summary_block = ""

            if _current_tokens() > self._budget_tokens:
                # Last resort: hard-truncate whatever verbatim window remains.
                ctx.recent_messages = ctx.recent_messages[-3:]
                logger.warning(
                    "context.budget_hard_truncated",
                    extra={"tokens": _current_tokens(), "budget": self._budget_tokens},
                )
        except Exception as exc:
            logger.debug("context.budget_check_skipped", extra={"reason": str(exc)})

    @staticmethod
    def _to_llm_messages(entries: list[dict]) -> list[LLMMessage]:
        """Convert raw history dicts to LLMMessage objects, remapping 'agent'→'assistant'."""
        messages: list[LLMMessage] = []
        for entry in entries:
            role = entry.get("role", "user")
            if role == "agent":
                role = "assistant"
            content = entry.get("content", "")
            if content:
                messages.append(LLMMessage(role=role, content=content))
        return messages


__all__ = ["CompressedContext", "ContextManager"]
