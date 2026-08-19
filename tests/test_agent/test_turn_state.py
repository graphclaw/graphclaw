# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_agent.test_turn_state — Tests for graphclaw.agent.turn_state and the
per-turn isolation it gives MainOrchestrator.

Covers two things:
1. TurnState / turn_scope / current_turn in isolation (contextvars mechanics).
2. The actual bug this fixes: MainOrchestrator is a process-wide singleton, and
   before this module, _current_caller_context / _current_session_id /
   _tool_registry were plain instance attributes — one concurrent user's turn
   could see or clobber another's. These tests run two "concurrent" turns via
   asyncio.gather and assert each only ever observes its own state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.agent.tool_registry import ToolSetRegistry
from graphclaw.agent.turn_state import TurnState, current_turn, turn_scope
from graphclaw.state.machine import StateMachine

# ---------------------------------------------------------------------------
# TurnState / turn_scope / current_turn — mechanics
# ---------------------------------------------------------------------------


def test_current_turn_is_none_outside_any_scope():
    assert current_turn() is None


def test_turn_scope_installs_state_for_its_duration():
    state = TurnState(user_id="U1")
    with turn_scope(state):
        assert current_turn() is state
    assert current_turn() is None


def test_turn_scope_resets_even_on_exception():
    state = TurnState(user_id="U1")
    with pytest.raises(RuntimeError):
        with turn_scope(state):
            raise RuntimeError("boom")
    assert current_turn() is None


def test_nested_turn_scopes_restore_outer_on_exit():
    outer = TurnState(user_id="outer")
    inner = TurnState(user_id="inner")
    with turn_scope(outer):
        assert current_turn() is outer
        with turn_scope(inner):
            assert current_turn() is inner
        assert current_turn() is outer
    assert current_turn() is None


async def test_turn_state_propagates_across_await():
    state = TurnState(user_id="U1")

    async def _inner() -> str | None:
        await asyncio.sleep(0)
        turn = current_turn()
        return turn.user_id if turn else None

    with turn_scope(state):
        result = await _inner()

    assert result == "U1"


async def test_two_concurrent_turn_scopes_do_not_leak_into_each_other():
    """The core contextvars guarantee: two tasks each running their own
    turn_scope must never observe the other's state, even though both run
    concurrently on the same event loop."""

    async def _run(user_id: str) -> str | None:
        state = TurnState(user_id=user_id)
        with turn_scope(state):
            await asyncio.sleep(0.01)
            turn = current_turn()
            return turn.user_id if turn else None

    results = await asyncio.gather(_run("alice"), _run("bob"))

    assert results == ["alice", "bob"]


# ---------------------------------------------------------------------------
# MainOrchestrator integration — fallback semantics outside a turn
# ---------------------------------------------------------------------------


def _make_loop() -> AgentLoop:
    repo = AsyncMock()
    repo._pool = None
    engine = MagicMock()
    engine.cache = MagicMock()
    return AgentLoop(graph_repo=repo, scoring_engine=engine, state_machine=StateMachine())


class TestFallbackOutsideTurnScope:
    """Outside any turn_scope() (background cycles, startup, CLI), the
    properties must behave exactly like the old plain instance attributes."""

    def test_current_session_id_fallback_roundtrips(self):
        loop = _make_loop()
        assert current_turn() is None

        loop._current_session_id = "ses-bg-1"

        assert loop._current_session_id == "ses-bg-1"

    def test_current_caller_context_fallback_roundtrips(self):
        loop = _make_loop()
        sentinel = object()

        loop._current_caller_context = sentinel

        assert loop._current_caller_context is sentinel

    def test_turn_delegation_calls_fallback_roundtrips(self):
        loop = _make_loop()

        loop._turn_delegation_calls = [{"task_id": "TSK-1"}]

        assert loop._turn_delegation_calls == [{"task_id": "TSK-1"}]

    def test_tool_registry_fallback_is_the_constructor_instance(self):
        """Outside a turn, self._tool_registry must be the same singleton
        ToolSetRegistry built in __init__ — preserves pre-refactor behaviour
        for every non-request code path (scoring cycles, etc.)."""
        loop = _make_loop()
        first = loop._tool_registry
        second = loop._tool_registry
        assert first is second
        assert isinstance(first, ToolSetRegistry)


class TestIsolationInsideTurnScope:
    """Inside a turn_scope(), the properties must read/write the TurnState,
    not the fallback instance attribute — and must not affect the fallback."""

    def test_caller_context_inside_scope_does_not_touch_fallback(self):
        loop = _make_loop()
        loop._current_caller_context = "fallback-value"

        state = TurnState(user_id="U1")
        with turn_scope(state):
            loop._current_caller_context = "turn-value"
            assert loop._current_caller_context == "turn-value"
            assert state.caller_context == "turn-value"

        # Fallback (used by background paths) is untouched.
        assert loop._current_caller_context == "fallback-value"

    def test_session_id_inside_scope_does_not_touch_fallback(self):
        loop = _make_loop()
        loop._current_session_id = "ses-fallback"

        with turn_scope(TurnState(user_id="U1")):
            loop._current_session_id = "ses-turn"
            assert loop._current_session_id == "ses-turn"

        assert loop._current_session_id == "ses-fallback"

    def test_tool_registry_inside_scope_is_fresh_and_lazy(self):
        loop = _make_loop()
        fallback_registry = loop._tool_registry

        with turn_scope(TurnState(user_id="U1")) as state:
            assert state.tool_registry is None  # not yet constructed
            turn_registry = loop._tool_registry
            assert turn_registry is not fallback_registry
            assert state.tool_registry is turn_registry
            # Same instance on repeated access within the same turn.
            assert loop._tool_registry is turn_registry

        # Fallback is untouched and still the original instance.
        assert loop._tool_registry is fallback_registry

    def test_delegation_calls_isolated_per_turn(self):
        loop = _make_loop()

        with turn_scope(TurnState(user_id="U1")):
            loop._turn_delegation_calls = [{"task_id": "TSK-A"}]
            assert loop._turn_delegation_calls == [{"task_id": "TSK-A"}]

        with turn_scope(TurnState(user_id="U2")):
            # A fresh TurnState starts with an empty delegation buffer,
            # regardless of what U1's turn left behind.
            assert loop._turn_delegation_calls == []


class TestConcurrentTurnsDoNotLeak:
    """End-to-end: two concurrent 'turns' on the SAME orchestrator instance
    (the real deployment shape — one process-wide singleton) must never see
    each other's caller_context, session_id, or tool_registry state."""

    async def test_two_concurrent_turns_isolated(self):
        loop = _make_loop()

        async def _run_turn(user_id: str, session_id: str) -> dict:
            state = TurnState(user_id=user_id, session_id=session_id)
            with turn_scope(state):
                loop._current_session_id = session_id
                loop._current_caller_context = f"caller-{user_id}"
                loop._tool_registry.activate("task_management")
                # Yield control so the other concurrent turn's code can run
                # in between — this is exactly the interleaving that used to
                # cause cross-user leakage on the old plain-attribute design.
                await asyncio.sleep(0.01)
                return {
                    "session_id": loop._current_session_id,
                    "caller_context": loop._current_caller_context,
                    "active_sets": set(loop._tool_registry.active_set_names),
                }

        result_a, result_b = await asyncio.gather(
            _run_turn("alice", "ses-alice"),
            _run_turn("bob", "ses-bob"),
        )

        assert result_a["session_id"] == "ses-alice"
        assert result_a["caller_context"] == "caller-alice"
        assert result_b["session_id"] == "ses-bob"
        assert result_b["caller_context"] == "caller-bob"

    async def test_one_turns_reset_session_does_not_strip_other_turns_tools(self):
        """Regression for the pre-fix bug: user B calling reset_session()
        (start of every chat turn) used to wipe out tool sets user A had
        just loaded, because both shared one ToolSetRegistry."""
        loop = _make_loop()

        async def _turn_a() -> list[str]:
            with turn_scope(TurnState(user_id="alice")):
                loop._tool_registry.activate("task_management")
                await asyncio.sleep(0.02)  # let B's reset_session() run
                tools = loop._tool_registry.get_active_tools()
                return [t.name for t in tools]

        async def _turn_b() -> None:
            await asyncio.sleep(0.01)
            with turn_scope(TurnState(user_id="bob")):
                loop._tool_registry.reset_session()

        tools_a, _ = await asyncio.gather(_turn_a(), _turn_b())

        # Alice's activated task_management tools must still be present after
        # Bob's concurrent reset_session() — they're on separate registries now.
        assert any(name in ("create_task", "update_task", "create_goal") for name in tools_a)
