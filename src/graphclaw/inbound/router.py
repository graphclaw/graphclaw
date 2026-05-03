"""graphclaw.inbound.router — Inbound message routing classification (FR-IN-001).

Description
-----------
Classifies every inbound message into one of five routes before further processing:

| Route                  | Meaning                                                   |
|------------------------|-----------------------------------------------------------|
| ``user_chat``          | Owner messaging their own agent.                          |
| ``counterparty_reply`` | Known counterparty replying on an existing tracked thread.|
| ``counterparty_proactive`` | Known counterparty opening a new/unsolicited thread.  |
| ``unknown_party``      | Sender not found in owner's identity substrate.           |
| ``drop``               | Receiving account not mapped to any owner.                |

Routing matrix (FR-IN-001):

| Sender match       | Reply-key match | Receiving account → owner | Route                  |
|--------------------|-----------------|---------------------------|------------------------|
| Owner's identity   | n/a             | yes                       | user_chat              |
| Known counterparty | yes             | yes                       | counterparty_reply     |
| Known counterparty | no              | yes                       | counterparty_proactive |
| Unknown sender     | n/a             | yes                       | unknown_party          |
| Any                | n/a             | no                        | drop                   |

Design Patterns
---------------
- Strategy: ``InboundRouter.classify()`` returns a ``RouteDecision``; callers
  switch on ``route`` without needing to know classification internals.
- Dependency Injection: registry + resolver + reply_key_store injected at
  construction time.

Public API
----------
- InboundRoute: Enum of valid routes.
- RouteDecision: Dataclass result of classification.
- InboundRouter: Classifier service.

Dependencies
------------
- graphclaw.gateway.agent_channel_identity: AgentChannelIdentityRegistry.
- graphclaw.inbound.reply_keys: ReplyKeyStore.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class InboundRoute(str, Enum):
    """Possible classifications for an inbound message."""

    USER_CHAT = "user_chat"
    COUNTERPARTY_REPLY = "counterparty_reply"
    COUNTERPARTY_PROACTIVE = "counterparty_proactive"
    UNKNOWN_PARTY = "unknown_party"
    DROP = "drop"


@dataclass
class RouteDecision:
    """Result of classifying an inbound message.

    Attributes
    ----------
    route:
        The classification outcome.
    owner_user_id:
        User ID of the agent's owner (empty for ``drop`` route).
    agent_id:
        Agent ID mapped to the receiving account (empty for ``drop``).
    counterparty_node_id:
        Resolved node ID for the sender (UserNode or ResourceNode).
        Empty when ``route == drop`` or ``route == unknown_party``.
    thread_id:
        Channel thread/conversation handle.
    channel:
        Channel identifier (e.g. ``"telegram"``, ``"email"``).
    reason:
        Human-readable explanation for the route decision.
    """

    route: InboundRoute
    owner_user_id: str = ""
    agent_id: str = ""
    counterparty_node_id: str = ""
    thread_id: str = ""
    channel: str = ""
    reason: str = ""


class InboundRouter:
    """Classifies inbound messages into route decisions (FR-IN-001).

    Parameters
    ----------
    channel_registry:
        ``AgentChannelIdentityRegistry`` mapping receiving accounts →
        ``(user_id, agent_id)``.
    counterparty_resolver:
        Object with ``resolve_to_node(channel, sender_id, owner_user_id) →
        str | None`` (FR-IN-002).  When ``None``, counterparty resolution is
        skipped (all unknown senders become ``unknown_party``).
    reply_key_store:
        ``ReplyKeyStore`` for checking whether a reply-key exists on the
        current thread.  When ``None``, reply-key check is skipped (known
        counterparty always becomes ``counterparty_proactive``).
    """

    def __init__(
        self,
        *,
        channel_registry: Any | None = None,
        counterparty_resolver: Any | None = None,
        reply_key_store: Any | None = None,
    ) -> None:
        self._registry = channel_registry
        self._resolver = counterparty_resolver
        self._reply_keys = reply_key_store

    async def classify(
        self,
        *,
        channel: str,
        sender_id: str,
        receiving_account: str,
        thread_id: str = "",
        msg_id: str = "",
    ) -> RouteDecision:
        """Classify an inbound message into a route decision.

        Parameters
        ----------
        channel:
            Channel identifier (e.g. ``"telegram"``, ``"email"``).
        sender_id:
            Channel-specific sender address (telegram user_id, email from-address).
        receiving_account:
            The account id of the bot/mailbox that received the message.
        thread_id:
            Channel thread/conversation handle (telegram chat_id, email thread-id).
        msg_id:
            Channel-specific message id (for reply-key lookup).

        Returns
        -------
        RouteDecision
        """
        # ── Step 1: Resolve receiving account → owner ────────────────────────
        owner_user_id, agent_id = await self._resolve_owner(receiving_account, channel)
        if not owner_user_id:
            return RouteDecision(
                route=InboundRoute.DROP,
                channel=channel,
                thread_id=thread_id,
                reason=f"No owner mapped to receiving account {receiving_account!r}",
            )

        # ── Step 2: Is sender the owner themselves? ──────────────────────────
        if await self._is_owner(sender_id, channel, owner_user_id):
            return RouteDecision(
                route=InboundRoute.USER_CHAT,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
                channel=channel,
                thread_id=thread_id,
                reason="Sender is owner",
            )

        # ── Step 3: Resolve counterparty node ────────────────────────────────
        counterparty_node_id = await self._resolve_counterparty(channel, sender_id, owner_user_id)
        if not counterparty_node_id:
            return RouteDecision(
                route=InboundRoute.UNKNOWN_PARTY,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
                channel=channel,
                thread_id=thread_id,
                reason=f"Sender {sender_id!r} not in identity substrate",
            )

        # ── Step 4: Is there an active reply key? ────────────────────────────
        reply_key_hit = await self._has_reply_key(channel, thread_id, msg_id)
        route = (
            InboundRoute.COUNTERPARTY_REPLY
            if reply_key_hit
            else InboundRoute.COUNTERPARTY_PROACTIVE
        )
        return RouteDecision(
            route=route,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            counterparty_node_id=counterparty_node_id,
            channel=channel,
            thread_id=thread_id,
            reason="reply-key hit" if reply_key_hit else "no reply-key (proactive)",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_owner(self, receiving_account: str, channel: str) -> tuple[str, str]:
        """Return ``(owner_user_id, agent_id)`` or ``("", "")`` when not mapped."""
        if self._registry is None:
            return "", ""
        try:
            entry = await self._registry.lookup(channel=channel, account_id=receiving_account)
            if entry is None:
                return "", ""
            return entry.user_id, entry.agent_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("InboundRouter: registry lookup failed: %s", exc)
            return "", ""

    async def _is_owner(self, sender_id: str, channel: str, owner_user_id: str) -> bool:
        """Return True when *sender_id* matches the owner's own identity on *channel*."""
        if self._registry is None:
            return False
        try:
            return await self._registry.is_owner_identity(
                user_id=owner_user_id, channel=channel, sender_id=sender_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("InboundRouter: is_owner_identity failed: %s", exc)
            return False

    async def _resolve_counterparty(self, channel: str, sender_id: str, owner_user_id: str) -> str:
        """Return counterparty node_id or empty string."""
        if self._resolver is None:
            return ""
        try:
            result = await self._resolver.resolve_to_node(channel, sender_id, owner_user_id)
            return result or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("InboundRouter: counterparty resolution failed: %s", exc)
            return ""

    async def _has_reply_key(self, channel: str, thread_id: str, msg_id: str) -> bool:
        """Return True when a reply-key exists for (channel, thread_id, msg_id)."""
        if self._reply_keys is None or not thread_id:
            return False
        try:
            record = await self._reply_keys.read_from_redis(channel, thread_id, msg_id)
            if record is not None:
                return True
            # Fallback: check Postgres
            record = await self._reply_keys.read_from_db(channel, thread_id)
            return record is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("InboundRouter: reply-key lookup failed: %s", exc)
            return False
