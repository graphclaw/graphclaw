"""GraphClaw state machine package."""
from __future__ import annotations

from graphclaw.state.cascade import activate_next_in_chain, check_composite_completion
from graphclaw.state.machine import StateMachine
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError

__all__ = [
    "StateMachine",
    "InvalidTransitionError",
    "VALID_TRANSITIONS",
    "check_composite_completion",
    "activate_next_in_chain",
]
