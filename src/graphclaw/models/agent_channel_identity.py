# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.models.agent_channel_identity — AgentChannelIdentity node model (FR-IN-003).

Description
-----------
Maps receiving accounts (Telegram bot id, email mailbox, WhatsApp number) to
``(user_id, agent_id)`` pairs.  This is the single source of truth for which
agent "owns" a given receiving address on a given channel.

Design Patterns
---------------
- Pydantic model: Schema-validated at write time by the admin API.

Public API
----------
- AgentChannelIdentity: Model for a single channel-to-agent mapping.

Dependencies
------------
- pydantic: BaseModel, Field.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentChannelIdentity(BaseModel):
    """Maps a receiving channel account to a user's agent (FR-IN-003).

    Parameters
    ----------
    user_id:
        Owner user ID.
    agent_id:
        Agent ID (typically same as user_id for the main comms agent).
    channel:
        Channel identifier: ``"telegram"``, ``"email"``, ``"whatsapp"``, etc.
    account_id:
        Channel-specific account handle: bot username, mailbox address, phone
        number, Slack workspace+channel id, etc.
    display_name:
        Human-readable label for admin UI.
    credentials_ref:
        Pointer to the secret holding channel credentials (secret name in
        SecretsClient, e.g. ``"telegram_bot_token_user_001"``).
    active:
        Whether this mapping is active.  Disabled entries cause inbound
        messages on this account to be ``drop``-routed.
    """

    user_id: str = Field(..., description="Owner user ID")
    agent_id: str = Field(..., description="Agent ID (usually == user_id)")
    channel: str = Field(..., description="Channel identifier (telegram/email/whatsapp/…)")
    account_id: str = Field(
        ..., description="Channel-specific account handle (bot name, mailbox, phone)"
    )
    display_name: str = Field(default="", description="Human-readable label")
    credentials_ref: str = Field(default="", description="Secret name for channel credentials")
    active: bool = Field(default=True, description="Whether this mapping is active")
    owner_identities: list[str] = Field(
        default_factory=list,
        description=(
            "List of channel-specific sender IDs that belong to the owner "
            "(used by is_owner_identity check in InboundRouter)."
        ),
    )
