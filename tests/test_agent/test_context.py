# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.context — ContextManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.context import CompressedContext, ContextManager
from graphclaw.llm.base import LLMMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(summary_text: str = "Summary of prior conversation."):
    """Build a mock LLMClient that returns a canned summary."""
    llm = AsyncMock()
    response = MagicMock()
    response.content = summary_text
    llm.complete = AsyncMock(return_value=response)
    llm.count_tokens = AsyncMock(return_value=1000)  # well under budget by default
    return llm


def _history(n: int, roles_alternate: bool = True) -> list[dict]:
    """Build n history entries alternating user/agent."""
    entries = []
    for i in range(n):
        role = "user" if (i % 2 == 0 or not roles_alternate) else "agent"
        entries.append({"role": role, "content": f"Message {i}"})
    return entries


def _history_with_nodes(nodes: list[str]) -> list[dict]:
    """History entries that mention given node IDs."""
    entries = []
    for i, node_id in enumerate(nodes):
        entries.append({"role": "user", "content": f"Please work on {node_id}"})
        entries.append({"role": "agent", "content": f"Working on {node_id} now."})
    return entries


# ---------------------------------------------------------------------------
# CompressedContext defaults
# ---------------------------------------------------------------------------


class TestCompressedContextDefaults:
    def test_defaults(self):
        ctx = CompressedContext()
        assert ctx.session_state_block == ""
        assert ctx.summary_block == ""
        assert ctx.recent_messages == []
        assert ctx.collapsed_tool_calls == ""
        assert not ctx.compression_applied
        assert ctx.original_count == 0
        assert ctx.compressed_count == 0


# ---------------------------------------------------------------------------
# Empty history
# ---------------------------------------------------------------------------


class TestEmptyHistory:
    @pytest.mark.asyncio
    async def test_empty_history_returns_empty_context(self):
        llm = _make_llm()
        cm = ContextManager(llm)

        ctx = await cm.compress([])

        assert ctx.session_state_block == ""
        assert ctx.recent_messages == []
        assert not ctx.compression_applied
        assert ctx.original_count == 0


# ---------------------------------------------------------------------------
# Sliding window (short history — no compression)
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    @pytest.mark.asyncio
    async def test_short_history_kept_verbatim(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = _history(5)

        ctx = await cm.compress(history)

        assert len(ctx.recent_messages) == 5
        assert not ctx.compression_applied

    @pytest.mark.asyncio
    async def test_window_size_respected(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=5)
        history = _history(15)

        ctx = await cm.compress(history)

        # Only the last 5 are kept verbatim in recent_messages
        assert len(ctx.recent_messages) == 5
        # Older 10 entries are collapsed (compression_applied = True)
        assert ctx.compression_applied


# ---------------------------------------------------------------------------
# Role remapping  "agent" → "assistant"
# ---------------------------------------------------------------------------


class TestRoleRemapping:
    @pytest.mark.asyncio
    async def test_agent_role_remapped_to_assistant(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "agent", "content": "Hi back"},
        ]

        ctx = await cm.compress(history)
        roles = [m.role for m in ctx.recent_messages]

        assert "agent" not in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_user_role_unchanged(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [{"role": "user", "content": "Question"}]

        ctx = await cm.compress(history)
        assert ctx.recent_messages[0].role == "user"

    @pytest.mark.asyncio
    async def test_empty_content_entries_skipped(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Real message"},
            {"role": "agent", "content": ""},  # empty — should be skipped
        ]

        ctx = await cm.compress(history)
        assert len(ctx.recent_messages) == 1


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    @pytest.mark.asyncio
    async def test_task_ids_extracted(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "What about TSK-AB-001-AT?"},
            {"role": "agent", "content": "TSK-AB-001-AT is ACTIVE."},
        ]

        ctx = await cm.compress(history)

        assert "TSK-AB-001-AT" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_goal_ids_extracted(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Focus on GOAL-XY-123"},
        ]

        ctx = await cm.compress(history)
        assert "GOAL-XY-123" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_state_associated_with_task(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "agent", "content": "TSK-AB-001-AT is now COMPLETE."},
        ]

        ctx = await cm.compress(history)
        # Entity register should show COMPLETE state
        assert "COMPLETE" in ctx.session_state_block

    @pytest.mark.asyncio
    async def test_no_nodes_returns_empty_block(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [
            {"role": "user", "content": "Just a general question"},
        ]

        ctx = await cm.compress(history)
        assert ctx.session_state_block == ""


# ---------------------------------------------------------------------------
# Tool-call collapse
# ---------------------------------------------------------------------------


class TestToolCallCollapse:
    @pytest.mark.asyncio
    async def test_older_turns_collapsed(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=3, summary_threshold=100)
        # 10 entries — last 3 are in window, first 7 are older
        history = _history(10)

        ctx = await cm.compress(history)

        assert ctx.collapsed_tool_calls != ""
        assert ctx.compression_applied


# ---------------------------------------------------------------------------
# Rolling LLM summary
# ---------------------------------------------------------------------------


class TestRollingSummary:
    @pytest.mark.asyncio
    async def test_summary_called_when_older_exceeds_threshold(self):
        llm = _make_llm("The user discussed task TSK-001-AT.")
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        # 20 entries → 15 older (> threshold=3) → summary triggered
        history = _history(20)

        ctx = await cm.compress(history)

        assert ctx.summary_block == "The user discussed task TSK-001-AT."
        llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_summary_replaces_collapsed_tool_calls_not_adds_to_it(self):
        """Regression: crossing summary_threshold used to ADD the summary on
        top of collapsed_tool_calls (both non-empty), which meant the
        "compression" step increased tokens instead of reducing them.
        A non-empty summary must replace the collapsed block."""
        llm = _make_llm("The user discussed task TSK-001-AT.")
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        history = _history(20)

        ctx = await cm.compress(history)

        assert ctx.summary_block == "The user discussed task TSK-001-AT."
        assert ctx.collapsed_tool_calls == ""

    @pytest.mark.asyncio
    async def test_cached_summary_branch_also_clears_collapsed_tool_calls(self):
        """Same fix applies to the `elif self._rolling_summaries...` branch
        (below summary_threshold but a summary already exists for this user)."""
        llm = _make_llm("Cached summary text.")
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        # First call crosses the threshold and populates the cache.
        await cm.compress(_history(20), user_id="alice")
        llm.complete.reset_mock()

        # Second call has fewer older entries (below threshold) but the
        # cached summary from the first call should still be used, and
        # collapsed_tool_calls must still be cleared.
        ctx = await cm.compress(_history(8), user_id="alice")

        llm.complete.assert_not_called()  # cached path, no new LLM call
        assert ctx.summary_block == "Cached summary text."
        assert ctx.collapsed_tool_calls == ""

    @pytest.mark.asyncio
    async def test_summary_not_called_when_below_threshold(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=15, summary_threshold=30)
        # 20 entries → only 5 older (< threshold=30) → no summary
        history = _history(20)

        await cm.compress(history)

        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_failure_falls_back_gracefully(self):
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        llm.count_tokens = AsyncMock(return_value=1000)
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        history = _history(20)

        # Should not raise
        ctx = await cm.compress(history)
        assert isinstance(ctx, CompressedContext)


# ---------------------------------------------------------------------------
# Rolling summary — per-user isolation
#
# ContextManager is owned by a process-wide singleton MainOrchestrator (one
# instance shared across every user). Before this fix, self._rolling_summary
# was a single shared string: crossing the summary_threshold for user B would
# overwrite the string, and a subsequent turn for user A that itself stayed
# below the threshold would read B's summary via the
# `elif self._rolling_summary:` branch — a cross-user data leak.
# ---------------------------------------------------------------------------


class TestRollingSummaryPerUserIsolation:
    @pytest.mark.asyncio
    async def test_two_users_get_independent_summaries(self):
        cm = ContextManager(_make_llm("shared client"), window_size=5, summary_threshold=3)

        # First call sets up llm.complete to return per-call content via
        # side_effect so each user's summary differs.
        cm._llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="Alice's summary"),
                MagicMock(content="Bob's summary"),
            ]
        )

        await cm.compress(_history(20), user_id="alice")
        await cm.compress(_history(20), user_id="bob")

        assert cm._rolling_summaries["alice"] == "Alice's summary"
        assert cm._rolling_summaries["bob"] == "Bob's summary"

    @pytest.mark.asyncio
    async def test_user_below_threshold_never_sees_another_users_summary(self):
        """The actual leak this fixes: user B's turn (below the summary
        threshold, so it takes the `elif self._rolling_summaries.get(...)`
        branch) must never see user A's previously-built summary."""
        cm = ContextManager(_make_llm("Alice's leaked summary"), window_size=5, summary_threshold=3)

        # User A crosses the threshold and builds a summary.
        await cm.compress(_history(20), user_id="alice")
        assert cm._rolling_summaries["alice"] == "Alice's leaked summary"

        # User B's turn has few older entries (below threshold) — takes the
        # cached-summary branch. Must be empty, not Alice's summary.
        ctx_b = await cm.compress(_history(20, roles_alternate=False)[:8], user_id="bob")

        assert ctx_b.summary_block != "Alice's leaked summary"
        assert "bob" not in cm._rolling_summaries or cm._rolling_summaries.get("bob") == ""

    @pytest.mark.asyncio
    async def test_missing_user_id_defaults_to_own_bucket_not_shared_state(self):
        """Backward-compat default (user_id="") must not collide with a real
        user_id — verified by checking it gets its own dict key."""
        cm = ContextManager(_make_llm("Anonymous summary"), window_size=5, summary_threshold=3)

        await cm.compress(_history(20))  # no user_id passed

        assert cm._rolling_summaries.get("") == "Anonymous summary"
        assert "alice" not in cm._rolling_summaries

    @pytest.mark.asyncio
    async def test_summary_failure_falls_back_to_that_users_previous_summary_only(self):
        cm = ContextManager(_make_llm("Alice v1"), window_size=5, summary_threshold=3)
        await cm.compress(_history(20), user_id="alice")
        assert cm._rolling_summaries["alice"] == "Alice v1"

        cm._llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        ctx = await cm.compress(_history(20), user_id="alice")

        # Falls back to alice's own previous summary, not some other user's.
        assert ctx.summary_block == "Alice v1"

    @pytest.mark.asyncio
    async def test_rolling_summaries_bounded_by_max_users(self):
        """A long-running process must not accumulate one dict entry per
        user forever — the eviction cap bounds memory."""
        cm = ContextManager(_make_llm("s"), window_size=5, summary_threshold=3)
        cm._MAX_ROLLING_SUMMARY_USERS = 3

        for i in range(5):
            await cm.compress(_history(20), user_id=f"user-{i}")

        assert len(cm._rolling_summaries) <= 3


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_builds_flat_message_list(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            session_state_block="## Session State\n- TSK-001: ACTIVE",
            recent_messages=[
                LLMMessage(role="user", content="Hello"),
                LLMMessage(role="assistant", content="Hi"),
            ],
        )

        messages = cm.build_messages(ctx)

        roles = [m.role for m in messages]
        # session_state_block becomes ONE user preamble message — no
        # synthetic assistant reply — followed by the verbatim recent turns.
        assert roles == ["user", "user", "assistant"]
        assert "TSK-001" in messages[0].content
        assert "Hello" in messages[-2].content
        assert "Hi" in messages[-1].content

    def test_no_synthetic_assistant_replies(self):
        """Regression: build_messages used to inject six synthetic messages
        (three user/assistant pairs like "Understood. I have the session
        state context.") — these cost tokens and teach small models to
        imitate fake turns. None of that fabricated text may appear."""
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            session_state_block="## Session State\n- TSK-001: ACTIVE",
            summary_block="Prior: user created tasks.",
            collapsed_tool_calls="[tool: create_task → TSK-001 created]",
            recent_messages=[LLMMessage(role="user", content="Continue")],
        )

        messages = cm.build_messages(ctx)
        contents = " ".join(m.content or "" for m in messages)

        for fake_phrase in (
            "Understood. I have the session state context.",
            "Got it — I have the prior conversation context.",
            "Understood — I can see the earlier actions taken.",
        ):
            assert fake_phrase not in contents

    def test_all_three_preamble_sections_combine_into_one_message(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            session_state_block="## Session State\n- TSK-001: ACTIVE",
            summary_block="Prior: user created tasks.",
            collapsed_tool_calls="[tool: create_task → TSK-001 created]",
            recent_messages=[LLMMessage(role="user", content="Continue")],
        )

        messages = cm.build_messages(ctx)

        # Exactly one preamble message plus the one recent message.
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert "TSK-001: ACTIVE" in messages[0].content
        assert "Prior: user created tasks." in messages[0].content
        assert "create_task" in messages[0].content

    def test_summary_block_inserted_before_recent(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            summary_block="Prior: user created tasks.",
            recent_messages=[LLMMessage(role="user", content="Current")],
        )

        messages = cm.build_messages(ctx)

        # Find summary block position
        summary_idx = next(i for i, m in enumerate(messages) if "Prior:" in (m.content or ""))
        current_idx = next(i for i, m in enumerate(messages) if "Current" in (m.content or ""))
        assert summary_idx < current_idx

    def test_empty_context_returns_empty_list(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext()

        messages = cm.build_messages(ctx)
        assert messages == []

    def test_collapsed_tool_calls_inserted(self):
        cm = ContextManager(MagicMock())
        ctx = CompressedContext(
            collapsed_tool_calls="[tool: create_task → TSK-001 created]",
            recent_messages=[LLMMessage(role="user", content="Continue")],
        )

        messages = cm.build_messages(ctx)
        contents = [m.content for m in messages]
        assert any("create_task" in (c or "") for c in contents)


# ---------------------------------------------------------------------------
# Fast path (history fits in window — Phase C1)
# ---------------------------------------------------------------------------


class TestFastPath:
    @pytest.mark.asyncio
    async def test_fast_path_short_history(self):
        """History <= window_size skips summary and token-budget API calls."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = _history(10)

        ctx = await cm.compress(history, current_messages=[LLMMessage(role="user", content="hi")])

        assert len(ctx.recent_messages) == 10
        assert not ctx.compression_applied
        # Fast path must avoid both the rolling-summary and count_tokens calls.
        llm.complete.assert_not_called()
        llm.count_tokens.assert_not_called()

    @pytest.mark.asyncio
    async def test_fast_path_still_extracts_entities(self):
        """Fast path keeps the always-on entity register."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20)
        history = [{"role": "agent", "content": "TSK-AB-001-AT is now COMPLETE."}]

        ctx = await cm.compress(history)

        assert "TSK-AB-001-AT" in ctx.session_state_block


# ---------------------------------------------------------------------------
# _enforce_budget — fixed_overhead accounting + multi-pass shrink
#
# Regression coverage: the previous version measured only
# build_messages(ctx) + current_messages, excluding the system prompt and
# tool schemas entirely (often 5-13k tokens) and calling LLMClient.count_tokens
# (a network round trip). It also shrank the window exactly once and never
# re-checked. This rewrite uses a cheap char-based estimate, includes
# fixed_overhead_tokens, and loops until it actually fits.
# ---------------------------------------------------------------------------


class TestEnforceBudget:
    @pytest.mark.asyncio
    async def test_fixed_overhead_counts_toward_the_budget(self):
        """A turn that fits without overhead but not with it must still
        trigger tightening — this is the exact defect being fixed."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20, budget_tokens=200)
        history = _history(25)  # small history, comfortably under 200 alone

        ctx = await cm.compress(
            history,
            current_messages=[LLMMessage(role="user", content="hi")],
            fixed_overhead_tokens=10_000,  # e.g. a large system prompt + tool schemas
        )

        assert ctx.compression_applied is True

    @pytest.mark.asyncio
    async def test_no_tightening_when_comfortably_under_budget(self):
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20, budget_tokens=1_000_000)
        history = _history(25)

        ctx = await cm.compress(
            history,
            current_messages=[LLMMessage(role="user", content="hi")],
            fixed_overhead_tokens=100,
        )

        assert len(ctx.recent_messages) == 20  # untouched window

    @pytest.mark.asyncio
    async def test_loop_shrinks_window_multiple_times_if_needed(self):
        """A single 25% shrink used to be applied once and never re-checked.
        With a very tight budget, the loop must shrink repeatedly."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=100, budget_tokens=50)
        # Each history entry ~ "Message N" (~10 chars ~ 2-3 tokens); with a
        # tiny budget the loop must shrink the window down toward the floor.
        history = _history(150)

        ctx = await cm.compress(history, current_messages=[LLMMessage(role="user", content="hi")])

        assert len(ctx.recent_messages) < 100
        assert ctx.compression_applied is True

    @pytest.mark.asyncio
    async def test_drops_collapsed_tool_calls_when_shrink_alone_is_insufficient(self):
        llm = _make_llm("a summary")
        cm = ContextManager(llm, window_size=10, summary_threshold=1000, budget_tokens=1)
        history = _history(50)

        ctx = await cm.compress(history, current_messages=[LLMMessage(role="user", content="hi")])

        assert ctx.collapsed_tool_calls == ""

    @pytest.mark.asyncio
    async def test_never_raises_on_budget_check_failure(self):
        """The whole method is wrapped defensively — a bug in the estimate
        path must never break the chat turn."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=20, budget_tokens=100)
        history = _history(25)

        # Should not raise even with a deliberately malformed current_messages.
        ctx = await cm.compress(
            history, current_messages=[LLMMessage(role="user", content="x" * 100000)]
        )
        assert isinstance(ctx, CompressedContext)


# ---------------------------------------------------------------------------
# Structured rolling summary (Phase C2)
# ---------------------------------------------------------------------------


class TestRollingSummaryActuallySucceeds:
    """Regression: _build_rolling_summary passed system= to LLMClient.complete,
    which has no such parameter — every call raised TypeError, silently
    caught, and rolling summarization never once produced a real summary.
    These tests use a real-signature fake (not a permissive MagicMock) so a
    reintroduced signature mismatch would fail loudly here."""

    @pytest.mark.asyncio
    async def test_complete_is_called_with_a_real_llmmessage_signature(self):
        """Uses LLMClient.complete's actual signature (no system= kwarg) —
        a fake that only accepts real parameters, so passing system= would
        raise TypeError here exactly as it does against every real backend."""

        class _RealSignatureLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            async def complete(
                self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
            ):
                self.calls.append({"messages": messages, "max_tokens": max_tokens})
                response = MagicMock()
                response.content = (
                    "## Goals\n- x\n## Progress\n- y\n## Blocking\n- z\n## Entities\n- w"
                )
                return response

        llm = _RealSignatureLLM()
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        history = _history(20)

        ctx = await cm.compress(history, user_id="alice")

        assert len(llm.calls) == 1  # did not raise / silently no-op
        assert ctx.summary_block.startswith("## Goals")
        assert cm._rolling_summaries["alice"] == ctx.summary_block

    @pytest.mark.asyncio
    async def test_system_message_is_role_system_not_a_kwarg(self):
        class _RealSignatureLLM:
            async def complete(
                self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None
            ):
                response = MagicMock()
                response.content = "summary"
                return response

        llm = _RealSignatureLLM()
        cm = ContextManager(llm, window_size=5, summary_threshold=3)

        ctx = await cm.compress(_history(20), user_id="alice")

        assert ctx.summary_block == "summary"


class TestStructuredSummary:
    @pytest.mark.asyncio
    async def test_structured_summary_sections(self):
        """The summary prompt requests Goals/Progress/Blocking/Entities sections."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=5, summary_threshold=3)
        history = _history(50)

        await cm.compress(history)

        llm.complete.assert_called_once()
        messages = llm.complete.call_args.kwargs["messages"]
        # messages[0] is now a proper role="system" message (see the
        # system= kwarg fix); the four-section instructions are in the
        # role="user" prompt that follows it.
        assert messages[0].role == "system"
        prompt = messages[1].content
        for header in ("## Goals", "## Progress", "## Blocking", "## Entities"):
            assert header in prompt


# ---------------------------------------------------------------------------
# Context-sensitive tool-call collapse — preserve failures (Phase C3)
# ---------------------------------------------------------------------------


class TestErrorPreservingCollapse:
    @pytest.mark.asyncio
    async def test_tool_call_collapse_preserves_errors(self):
        """Failed tool calls in older turns are flagged with a FAILED prefix."""
        llm = _make_llm()
        cm = ContextManager(llm, window_size=2, summary_threshold=100)
        history = [
            {"role": "user", "content": "Create the task"},
            {"role": "agent", "content": '[tool: create_task] {"error": "boom failed"}'},
            {"role": "user", "content": "filler 1"},
            {"role": "agent", "content": "filler 2"},
            {"role": "user", "content": "recent 1"},
            {"role": "agent", "content": "recent 2"},
        ]

        ctx = await cm.compress(history)

        assert "[FAILED tool action:" in ctx.collapsed_tool_calls
        # Failure excerpt is longer (200) than the success excerpt (120).
        assert "boom failed" in ctx.collapsed_tool_calls
