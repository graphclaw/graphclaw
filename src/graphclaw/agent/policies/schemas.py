"""graphclaw.agent.policies.schemas — Typed Pydantic schemas for policy frontmatter.

Description
-----------
Each ``.md`` policy file has a YAML frontmatter block validated against one of
these schemas.  Unknown fields are ignored to allow forward-compatible additions.

Public API
----------
- FailMode: ``closed`` | ``degraded`` — behaviour when policy fails to load.
- DelegationPolicy: what the agent may do unsupervised on the owner's behalf.
- EscalationPolicy: when the agent must interrupt the owner.
- CounterpartyEtiquettePolicy: tone/conventions for counterparty-facing comms.
- ReplyTonePolicy: voice for outbound drafting.
- POLICY_SCHEMA_MAP: mapping from policy filename (without .md) to schema class.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FailMode(str, Enum):
    """Behaviour when a policy file cannot be loaded or parsed."""

    CLOSED = "closed"
    """Refuse the outbound action — safe default for delegation/escalation."""

    DEGRADED = "degraded"
    """Continue without policy constraints — acceptable for etiquette/tone."""


class AllowedTransition(BaseModel):
    """A single allowed state transition entry in DelegationPolicy."""

    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")

    model_config = {"populate_by_name": True}


class RecipientOverride(BaseModel):
    """Per-recipient override block in DelegationPolicy."""

    accept_deadline_extension_max_days: int | None = None
    escalate_on_blocker: bool | None = None


class DelegationPolicy(BaseModel):
    """Parsed frontmatter for delegation.md (FR-POL-001).

    Controls what actions the agent may take unsupervised on the owner's behalf.
    """

    fail_mode: FailMode = FailMode.CLOSED
    auto_acknowledge: bool = True
    accept_deadline_extension_max_days: int = 3
    allowed_state_transitions: list[AllowedTransition] = Field(default_factory=list)
    escalate_on_blocker: bool = True
    recipient_overrides: dict[str, RecipientOverride] = Field(default_factory=dict)

    model_config = {"extra": "ignore", "populate_by_name": True}


class EscalationPolicy(BaseModel):
    """Parsed frontmatter for escalation.md (FR-POL-001)."""

    fail_mode: FailMode = FailMode.CLOSED
    escalate_on_deadline_miss: bool = True
    escalate_on_blocked_task: bool = True
    interrupt_threshold: float = 0.8
    quiet_hours_escalate: bool = False

    model_config = {"extra": "ignore"}


class CounterpartyEtiquettePolicy(BaseModel):
    """Parsed frontmatter for counterparty_etiquette.md (FR-POL-001)."""

    fail_mode: FailMode = FailMode.DEGRADED
    max_follow_ups: int = 3
    follow_up_gap_days: int = 3
    tone: str = "professional"  # "formal" | "professional" | "casual"
    sign_off: str | None = None  # e.g. "Best, {{owner_name}}"

    model_config = {"extra": "ignore"}


class ReplyTonePolicy(BaseModel):
    """Parsed frontmatter for reply_tone.md (FR-POL-001)."""

    fail_mode: FailMode = FailMode.DEGRADED
    voice: str = "neutral"  # "first_person" | "third_person" | "neutral"
    brevity: str = "medium"  # "terse" | "medium" | "verbose"
    emoji_allowed: bool = False

    model_config = {"extra": "ignore"}


# Map policy_name (no .md extension) to its Pydantic schema class.
POLICY_SCHEMA_MAP: dict[str, type] = {
    "delegation": DelegationPolicy,
    "escalation": EscalationPolicy,
    "counterparty_etiquette": CounterpartyEtiquettePolicy,
    "reply_tone": ReplyTonePolicy,
}

# Canonical policy names for seeding and iteration.
CANONICAL_POLICY_NAMES: list[str] = list(POLICY_SCHEMA_MAP.keys())
