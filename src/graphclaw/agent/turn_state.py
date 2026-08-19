# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.turn_state — Per-request state, isolated via contextvars.

Description
-----------
``MainOrchestrator`` is a single process-wide singleton (constructed once at
gateway startup, `app.state.agent_loop`). Before this module, several pieces
of per-turn state — the ACL principal (``_current_caller_context``), the
active session id, the buffered conversation history, and the set of loaded
tool sets — lived directly on that singleton as plain instance attributes.
Under concurrency this is a correctness and privacy bug, not just an
inconvenience: user A's ``CallerContext`` (an authorization principal) could
be overwritten by user B's concurrent turn between the write and a tool's
read, and a tool set user A loaded could still be visible to user B's
in-flight turn.

``TurnState`` + :func:`turn_scope` fix this by moving that state into a
``contextvars.ContextVar``, which propagates correctly across ``await`` and
into ``asyncio.ensure_future`` (each spawned task gets its own copy of the
context at creation time), but is naturally isolated between concurrent
top-level requests — each request runs in its own ``asyncio.Task`` with its
own context.

Design Patterns
---------------
- Context Object: ``TurnState`` bundles everything that must be request-scoped
  rather than process-scoped.
- ContextVar + explicit scope: ``turn_scope`` is the only way to install a
  ``TurnState`` — callers cannot forget to reset it (the ``finally`` block
  always runs), and nested/re-entrant scopes are supported since each call
  captures its own reset token.

Public API
----------
- TurnState: per-request state bundle.
- current_turn: return the active TurnState, or None outside any scope.
- turn_scope: context manager that installs a TurnState for its duration.

Dependencies
------------
- contextvars: ContextVar (stdlib).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.agent.tool_registry import ToolSetRegistry
    from graphclaw.cross_tenant.acl import CallerContext


@dataclass
class TurnState:
    """Everything about one in-flight chat/counterparty turn that must not
    leak into a concurrent turn for a different user.

    Attributes:
        user_id: Whose turn this is. Present mainly for logging/assertions —
            callers already have it in scope, but having it on the state
            object makes cross-checking trivial in tests.
        session_id: The active session id for structured-logging correlation.
        caller_context: The ACL principal tools execute under. This is the
            attribute whose process-global mutation was the actual security
            bug this module fixes.
        conversation_history: The turn's working copy of chat history (the
            orchestrator appends the current user message to the caller-
            supplied history before compression).
        delegation_calls: Buffer for ``delegate_to_agent`` calls made within
            this turn, consumed by the dispatch-planner pre-pass.
        tool_registry: A fresh ``ToolSetRegistry`` for this turn only — the
            previous single shared registry meant one user's
            ``load_tool_set`` call was visible to every other concurrent
            user's turn, and ``reset_session()`` on one turn could strip
            tool sets out from under another turn's in-flight loop.
    """

    user_id: str
    session_id: str | None = None
    caller_context: CallerContext | None = None
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    delegation_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_registry: ToolSetRegistry | None = None


_TURN: ContextVar[TurnState | None] = ContextVar("graphclaw_turn", default=None)


def current_turn() -> TurnState | None:
    """Return the active :class:`TurnState`, or ``None`` outside any scope.

    Deliberately does not raise when unset: background paths (the periodic
    scoring cycle, startup seeding, CLI one-shot calls) run outside any HTTP
    request and must keep working against the orchestrator's own fallback
    instance state — see the ``_current_*`` properties on
    ``MainOrchestrator`` for how callers fall back gracefully.
    """
    return _TURN.get()


@contextmanager
def turn_scope(state: TurnState) -> Iterator[TurnState]:
    """Install *state* as the active :class:`TurnState` for the ``with`` body.

    Always resets on exit, including on exception, so a turn's state can
    never leak into whatever runs next on the same task/thread.
    """
    token = _TURN.set(state)
    try:
        yield state
    finally:
        _TURN.reset(token)
