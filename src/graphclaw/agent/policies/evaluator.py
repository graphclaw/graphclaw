# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.policies.evaluator — Pre-LLM policy gate for outbound actions.

Description
-----------
Stateless evaluation functions that check an outbound intent against the parsed
DelegationPolicy before any LLM call is made (FR-OUT-003).

Design Patterns
---------------
- Pure function: no side effects; takes intent + loaded policy; returns decision.
- AllowOrEscalate: typed result discriminated on ``decision`` field.

Public API
----------
- OutboundIntent: intent descriptor passed to the gate.
- PolicyDecision: ``"allow"`` | ``"escalate"``
- EvaluationResult: result with decision + reason.
- evaluate_outbound_intent: main gate function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from graphclaw.agent.policies.schemas import DelegationPolicy

PolicyDecision = Literal["allow", "escalate"]


@dataclass
class OutboundIntent:
    """Describes an outbound action the agent wants to take."""

    task_id: str | None
    recipient_id: str
    purpose: str  # free-text description of intent
    proposed_state_transition: tuple[str, str] | None = None  # (from, to)
    deadline_extension_days: int | None = None
    draft: str | None = None  # optional message draft


@dataclass
class EvaluationResult:
    """Result of evaluating an outbound intent against a policy."""

    decision: PolicyDecision
    reason: str
    policy_name: str = "delegation"
    violations: list[str] = field(default_factory=list)


def evaluate_outbound_intent(
    intent: OutboundIntent,
    policy: DelegationPolicy,
) -> EvaluationResult:
    """Gate an outbound intent against the DelegationPolicy.

    Rules checked (in order):
    1. State transition: must be in policy.allowed_state_transitions (if any defined).
    2. Deadline extension: proposed days ≤ accept_deadline_extension_max_days
       (per-recipient override takes precedence).
    3. Blocker escalation: if escalate_on_blocker=True and purpose contains
       'blocker' → escalate.

    Parameters
    ----------
    intent:
        The outbound action being proposed.
    policy:
        The parsed DelegationPolicy for this user+agent.

    Returns
    -------
    EvaluationResult with decision=``"allow"`` or ``"escalate"``.
    """
    violations: list[str] = []

    # 1. Per-recipient override lookup.
    override = policy.recipient_overrides.get(intent.recipient_id)

    # 2. State transition check.
    if intent.proposed_state_transition and policy.allowed_state_transitions:
        from_s, to_s = intent.proposed_state_transition
        allowed = any(
            t.from_state == from_s and t.to_state == to_s for t in policy.allowed_state_transitions
        )
        if not allowed:
            violations.append(f"State transition {from_s}→{to_s} not in allowed_state_transitions")

    # 3. Deadline extension check.
    if intent.deadline_extension_days is not None:
        max_days = (
            override.accept_deadline_extension_max_days
            if override and override.accept_deadline_extension_max_days is not None
            else policy.accept_deadline_extension_max_days
        )
        if intent.deadline_extension_days > max_days:
            violations.append(
                f"Deadline extension {intent.deadline_extension_days}d exceeds "
                f"policy limit {max_days}d"
            )

    # 4. Blocker escalation.
    escalate_on_blocker = (
        override.escalate_on_blocker
        if override and override.escalate_on_blocker is not None
        else policy.escalate_on_blocker
    )
    if escalate_on_blocker and "blocker" in intent.purpose.lower():
        violations.append("Purpose contains 'blocker' and escalate_on_blocker=True")

    if violations:
        return EvaluationResult(
            decision="escalate",
            reason="; ".join(violations),
            violations=violations,
        )

    return EvaluationResult(
        decision="allow",
        reason="All delegation policy checks passed",
    )
