# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.outbound_intent — OutboundIntent data model.

Description
-----------
``OutboundIntent`` carries the minimal context the Comms agent passes to the
``OutboundCommunicationAgent`` to initiate an outbound dispatch.  The model is
intentionally lean — channel resolution, policy enforcement, and drafting live
inside the outbound agent.

Design Patterns
---------------
- Value Object: Immutable descriptor passed across agent boundaries.
- Pydantic model: Validated at the call boundary; no logic inside.

Public API
----------
- OutboundIntent: Intent model for outbound dispatch.

Dependencies
------------
- pydantic: BaseModel, Field.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutboundIntent(BaseModel):
    """Intent descriptor passed from Comms agent → OutboundCommunicationAgent.

    Parameters
    ----------
    task_id:
        Optional task this outbound message is about.
    recipient_id:
        Node ID (UserNode or ResourceNode) of the recipient.
    purpose:
        Short description of why this message is being sent.
    draft:
        Optional pre-written draft text.  When provided the outbound agent may
        use it directly or refine it before dispatching.
    channel_override:
        When set, skip channel resolution and dispatch on this channel.
    proposed_state_transition:
        Optional state transition the agent wants to communicate (e.g. moving a
        task from WAITING → IN_PROGRESS).  Evaluated by delegation policy.
    deadline_extension_days:
        Number of days by which the deadline is being extended.  Evaluated by
        delegation policy.
    """

    task_id: str | None = Field(default=None, description="Task this message is about")
    recipient_id: str = Field(..., description="UserNode or ResourceNode id")
    purpose: str = Field(..., description="Why the message is being sent")
    draft: str | None = Field(default=None, description="Pre-written draft text")
    channel_override: str | None = Field(default=None, description="Force dispatch on this channel")
    proposed_state_transition: tuple[str, str] | None = Field(
        default=None,
        description="(from_state, to_state) tuple evaluated by delegation policy",
    )
    deadline_extension_days: int = Field(
        default=0, description="Deadline extension days (delegation policy check)"
    )
    escalate_on_blocker: bool = Field(
        default=False, description="Whether a blocker is being raised"
    )
