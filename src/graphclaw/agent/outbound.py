# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.outbound — OutboundDispatcher + OutboundCommunicationAgent.

Description
-----------
Two classes in this module:

1. **OutboundDispatcher** (original) — low-level channel routing.  Routes
   agent-initiated messages directly to EmailSender / TelegramSender or
   publishes to the OUTBOUND_MESSAGES broker queue as a fallback.

2. **OutboundCommunicationAgent** (Wave 2, FR-OUT-001..004) — high-level peer
   agent that wraps OutboundDispatcher with:
   - Channel resolution from recipient preferences + channel stickiness (FR-OUT-002).
   - Delegation policy enforcement via ``evaluate_outbound_intent`` (FR-OUT-003).
   - Post-dispatch CheckinNode creation, Redis reply-key, and intelligence write
     (FR-OUT-004).

Design Patterns
---------------
- Strategy: Channel selection is resolved at send time based on the ``channel``
  parameter or the user's registered preferences, making it trivial to add
  new channels (WhatsApp, Slack, etc.) without changing calling code.
- Dependency Injection: All adapters and the broker are injected; no global
  state.
- Graceful Degradation: If a channel adapter is unavailable the dispatcher
  logs a warning and skips that channel rather than raising.
- Decorator / Wrapper: OutboundCommunicationAgent wraps OutboundDispatcher
  adding policy, resolution and post-dispatch hooks.

Public API
----------
- OutboundDispatcher: Low-level channel routing class.
- OutboundCommunicationAgent: High-level peer agent (Wave 2).

Dependencies
------------
- graphclaw.gateway.channels.email.sender: EmailSender.
- graphclaw.gateway.channels.telegram.sender: TelegramSender.
- graphclaw.gateway.channels.telegram.config: TelegramConfig.
- graphclaw.infra.broker: MessageBroker, OUTBOUND_MESSAGES.
- graphclaw.agent.outbound_intent: OutboundIntent.
- graphclaw.agent.policies.evaluator: evaluate_outbound_intent.
- graphclaw.agent.policies.loader: PolicyLoader.
- graphclaw.inbound.reply_keys: ReplyKeyStore, ReplyKeyRecord.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphclaw.gateway.channels.email.sender import EmailSender
    from graphclaw.gateway.channels.telegram.sender import TelegramSender
    from graphclaw.infra.broker import MessageBroker

logger = logging.getLogger(__name__)


class OutboundDispatcher:
    """Routes agent-initiated messages to the correct channel adapter.

    Parameters
    ----------
    email_sender:
        Optional :class:`~graphclaw.gateway.channels.email.sender.EmailSender`
        for direct SMTP dispatch.
    telegram_sender:
        Optional :class:`~graphclaw.gateway.channels.telegram.sender.TelegramSender`
        for direct Telegram Bot API dispatch.
    broker:
        Optional :class:`~graphclaw.infra.broker.MessageBroker` for queue-based
        dispatch (used when direct adapters are not injected).
    from_email:
        The "From" address used when sending email (e.g. the agent's gmail address).
    """

    def __init__(
        self,
        email_sender: EmailSender | None = None,
        telegram_sender: TelegramSender | None = None,
        broker: MessageBroker | None = None,
        from_email: str | None = None,
    ) -> None:
        self._email = email_sender
        self._telegram = telegram_sender
        self._broker = broker
        self._from_email = from_email or os.environ.get("GATEWAY_SMTP_USER", "")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> None:
        """Send an email message.

        Tries direct EmailSender first; falls back to OUTBOUND_MESSAGES queue.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        body:
            Plain-text email body.
        in_reply_to:
            Optional Message-ID of the email being replied to.
        """
        if self._email is not None:
            try:
                await self._email.send(
                    recipient=to,
                    subject=subject,
                    body=body,
                    in_reply_to=in_reply_to,
                )
                logger.info("OutboundDispatcher: email sent to %s via direct adapter", to)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OutboundDispatcher: direct email send failed: %s — falling back to queue", exc
                )

        if self._broker is not None:
            from graphclaw.infra.broker import OUTBOUND_MESSAGES

            payload = json.dumps(
                {
                    "channel": "email",
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "in_reply_to": in_reply_to,
                }
            )
            await self._broker.publish(OUTBOUND_MESSAGES, payload)
            logger.info("OutboundDispatcher: email queued for %s via broker", to)
            return

        logger.warning("OutboundDispatcher: no email adapter or broker — email to %s dropped", to)

    async def send_telegram(self, chat_id: str, text: str) -> None:
        """Send a Telegram message to *chat_id*.

        Parameters
        ----------
        chat_id:
            Telegram chat ID (numeric string or @username).
        text:
            Message text (max 4096 chars).
        """
        if self._telegram is not None:
            try:
                await self._telegram.send(chat_id=chat_id, text=text)
                logger.info("OutboundDispatcher: telegram sent to chat_id=%s", chat_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutboundDispatcher: telegram send failed: %s", exc)
                return

        if self._broker is not None:
            from graphclaw.infra.broker import OUTBOUND_MESSAGES

            payload = json.dumps({"channel": "telegram", "chat_id": chat_id, "text": text})
            await self._broker.publish(OUTBOUND_MESSAGES, payload)
            logger.info("OutboundDispatcher: telegram queued for chat_id=%s via broker", chat_id)
            return

        logger.warning(
            "OutboundDispatcher: no telegram adapter or broker — message to %s dropped", chat_id
        )

    async def send(
        self,
        channel: str,
        *,
        to: str,
        subject: str = "",
        body: str,
    ) -> None:
        """Route a message to the specified channel.

        Parameters
        ----------
        channel:
            One of ``"email"`` or ``"telegram"``.
        to:
            Recipient address: email address for ``"email"``, chat_id for
            ``"telegram"``.
        subject:
            Subject line (email only; ignored for Telegram).
        body:
            Message content.
        """
        if channel == "email":
            await self.send_email(to=to, subject=subject, body=body)
        elif channel == "telegram":
            await self.send_telegram(chat_id=to, text=body)
        else:
            logger.warning("OutboundDispatcher.send: unknown channel %r — message dropped", channel)

    async def broadcast(
        self,
        channels: list[dict[str, Any]],
        subject: str = "",
        body: str = "",
    ) -> None:
        """Send the same message across multiple channels.

        Parameters
        ----------
        channels:
            List of channel descriptors, e.g.
            ``[{"channel": "email", "to": "user@example.com"},
               {"channel": "telegram", "to": "12345678"}]``.
        subject:
            Subject line (email only).
        body:
            Message content.
        """
        for ch in channels:
            await self.send(
                ch.get("channel", ""),
                to=ch.get("to", ""),
                subject=subject,
                body=body,
            )

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, broker: MessageBroker | None = None) -> OutboundDispatcher:
        """Construct an OutboundDispatcher wired from environment variables.

        Creates an ``EmailSender`` from SMTP env vars and a ``TelegramSender``
        from the bot token env var.  Both are optional — missing credentials
        cause the respective adapter to be omitted and messages fall back to
        broker queue dispatch.

        Parameters
        ----------
        broker:
            Optional MessageBroker; used as queue fallback when direct adapter
            send fails.
        """
        email_sender = None
        telegram_sender = None

        # Email sender
        smtp_user = os.environ.get("GATEWAY_SMTP_USER", "")
        smtp_pass = os.environ.get("GATEWAY_SMTP_PASS", "")
        smtp_host = os.environ.get("GATEWAY_SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("GATEWAY_SMTP_PORT", "587"))
        if smtp_user and smtp_pass:
            try:
                from graphclaw.gateway.channels.email.sender import EmailSender  # noqa: PLC0415

                # Port 587 uses STARTTLS; port 465 uses implicit TLS (SMTPS).
                use_tls = smtp_port == 465
                start_tls = smtp_port == 587
                email_sender = EmailSender(
                    host=smtp_host,
                    port=smtp_port,
                    username=smtp_user,
                    password=smtp_pass,
                    use_tls=use_tls,
                    start_tls=start_tls,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("OutboundDispatcher.from_env: could not create EmailSender: %s", exc)

        # Telegram sender
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if bot_token:
            try:
                from graphclaw.gateway.channels.telegram.config import (
                    TelegramConfig,  # noqa: PLC0415
                )
                from graphclaw.gateway.channels.telegram.sender import (
                    TelegramSender,  # noqa: PLC0415
                )

                tg_config = TelegramConfig(bot_token=bot_token)
                telegram_sender = TelegramSender(config=tg_config)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OutboundDispatcher.from_env: could not create TelegramSender: %s", exc
                )

        return cls(
            email_sender=email_sender,
            telegram_sender=telegram_sender,
            broker=broker,
            from_email=smtp_user,
        )


# ─────────────────────────────────────────────────────────────────────────────
# OutboundCommunicationAgent (Wave 2 — FR-OUT-001..004)
# ─────────────────────────────────────────────────────────────────────────────


class DispatchResult:
    """Outcome of a single OutboundCommunicationAgent.send() call.

    Attributes
    ----------
    checkin_id:
        ID of the CheckinNode created after dispatch (or None on failure).
    channel:
        Channel used for dispatch (e.g. 'email', 'telegram').
    thread_id:
        Thread ID used for this dispatch.
    escalated:
        ``True`` when delegation policy mandated escalation.
    escalate_reason:
        Human-readable reason for escalation (empty when not escalated).
    error:
        Error message when dispatch failed.
    """

    def __init__(
        self,
        *,
        checkin_id: str | None = None,
        channel: str = "",
        thread_id: str = "",
        escalated: bool = False,
        escalate_reason: str = "",
        error: str = "",
    ) -> None:
        self.checkin_id = checkin_id
        self.channel = channel
        self.thread_id = thread_id
        self.escalated = escalated
        self.escalate_reason = escalate_reason
        self.error = error


class OutboundCommunicationAgent:
    """High-level outbound peer agent (FR-OUT-001..004).

    Wraps ``OutboundDispatcher`` with:
    - Channel resolution from recipient preferences + channel stickiness (FR-OUT-002).
    - Delegation policy enforcement via ``evaluate_outbound_intent`` (FR-OUT-003).
    - Post-dispatch CheckinNode creation, Redis reply-key, and intelligence write
      (FR-OUT-004).

    Parameters
    ----------
    dispatcher:
        Low-level ``OutboundDispatcher`` for the actual channel send.
    graph_store:
        AGE graph store for recipient node lookup and CheckinNode creation.
    policy_loader:
        ``PolicyLoader`` to fetch the owner's delegation policy.
    reply_key_store:
        ``ReplyKeyStore`` for dual-write reply keys (Redis + Postgres).
    owner_user_id:
        User ID of the owner this agent acts on behalf of.
    agent_id:
        Agent ID (typically same as owner_user_id for the main agent).
    default_stickiness_hours:
        How long an active thread on another channel takes priority.  Defaults
        to 48 hours (reads from UserNode preferences if available).
    """

    def __init__(
        self,
        dispatcher: OutboundDispatcher,
        *,
        graph_store: Any | None = None,
        policy_loader: Any | None = None,
        reply_key_store: Any | None = None,
        owner_user_id: str = "",
        agent_id: str = "",
        default_stickiness_hours: int = 48,
    ) -> None:
        self._dispatcher = dispatcher
        self._graph_store = graph_store
        self._policy_loader = policy_loader
        self._reply_key_store = reply_key_store
        self._owner_user_id = owner_user_id
        self._agent_id = agent_id
        self._default_stickiness_hours = default_stickiness_hours

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(
        self,
        intent: Any,  # OutboundIntent — avoids circular import; typed at runtime
        *,
        caller_context: Any | None = None,
    ) -> DispatchResult:
        """Dispatch an outbound message described by *intent*.

        Steps:
        1. Enforce delegation policy (FR-OUT-003).  Return DispatchResult with
           ``escalated=True`` if the policy rejects the intent.
        2. Resolve channel (FR-OUT-002).
        3. Dispatch via OutboundDispatcher.
        4. Create CheckinNode + write Redis reply-key (FR-OUT-004).
        5. Append intelligence line on the task node (FR-OUT-004).

        Parameters
        ----------
        intent:
            ``OutboundIntent`` describing the dispatch.
        caller_context:
            Optional caller context for graph operations.

        Returns
        -------
        DispatchResult
        """
        from graphclaw.agent.outbound_intent import OutboundIntent  # noqa: PLC0415

        if not isinstance(intent, OutboundIntent):
            raise TypeError(f"Expected OutboundIntent, got {type(intent)}")

        # ── Step 1: Delegation policy enforcement (FR-OUT-003) ───────────────
        escalation_result = await self._check_delegation_policy(intent)
        if escalation_result is not None and escalation_result.decision == "escalate":
            logger.info(
                "OutboundCommunicationAgent: escalating intent for recipient=%s reason=%s",
                intent.recipient_id,
                escalation_result.reason,
            )
            return DispatchResult(
                escalated=True,
                escalate_reason=escalation_result.reason,
            )

        # ── Step 2: Channel resolution (FR-OUT-002) ──────────────────────────
        channel, thread_id, to_address = await self._resolve_channel(intent)

        # ── Step 3: Dispatch ─────────────────────────────────────────────────
        subject = f"Re: {intent.purpose}" if thread_id else intent.purpose
        body = intent.draft or intent.purpose
        try:
            await self._dispatcher.send(channel, to=to_address, subject=subject, body=body)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OutboundCommunicationAgent: dispatch failed channel=%s to=%s: %s",
                channel,
                to_address,
                exc,
            )
            return DispatchResult(error=str(exc), channel=channel, thread_id=thread_id)

        # ── Step 4: CheckinNode + reply-key (FR-OUT-004) ─────────────────────
        checkin_id = await self._create_checkin(
            intent=intent, channel=channel, thread_id=thread_id, to_address=to_address
        )
        await self._write_reply_key(
            intent=intent,
            channel=channel,
            thread_id=thread_id,
            checkin_id=checkin_id or "",
        )

        # ── Step 5: Intelligence write (FR-OUT-004) ──────────────────────────
        if intent.task_id and self._graph_store is not None:
            await self._append_intelligence(
                task_id=intent.task_id,
                channel=channel,
                recipient_id=intent.recipient_id,
                purpose=intent.purpose,
            )

        return DispatchResult(
            checkin_id=checkin_id,
            channel=channel,
            thread_id=thread_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_delegation_policy(self, intent: Any) -> Any | None:
        """Evaluate delegation policy for *intent*.  Returns EvaluationResult or None."""
        if self._policy_loader is None:
            return None
        try:
            from graphclaw.agent.policies.evaluator import (  # noqa: PLC0415
                OutboundIntent as PolicyIntent,
            )
            from graphclaw.agent.policies.evaluator import (
                evaluate_outbound_intent,
            )
            from graphclaw.agent.policies.loader import PolicyLoadError  # noqa: PLC0415

            try:
                loaded = await self._policy_loader.load(
                    self._owner_user_id, self._agent_id, "delegation"
                )
                policy_obj = loaded.schema_obj
            except PolicyLoadError:
                return None  # closed + missing → evaluator already handled at loader level

            policy_intent = PolicyIntent(
                task_id=intent.task_id or "",
                recipient_id=intent.recipient_id,
                purpose=intent.purpose,
                proposed_state_transition=intent.proposed_state_transition,
                deadline_extension_days=intent.deadline_extension_days,
            )
            return evaluate_outbound_intent(policy_intent, policy_obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutboundCommunicationAgent: policy check failed: %s", exc)
            return None

    async def _resolve_channel(self, intent: Any) -> tuple[str, str, str]:
        """Return (channel, thread_id, to_address) for *intent*.

        Channel resolution order (FR-OUT-002):
        1. channel_override in intent → use directly.
        2. recipient.preferences.preferred_channel from graph.
        3. Active CheckinNode thread within stickiness window → use that channel.
        4. Default: "email".

        ``to_address`` is the channel-specific address string (email, chat_id, …).
        ``thread_id`` is the active thread id when stickiness applies, else "".
        """
        if intent.channel_override:
            to_address = await self._get_channel_address(
                intent.recipient_id, intent.channel_override
            )
            return intent.channel_override, "", to_address

        preferred: str = "email"
        if self._graph_store is not None:
            try:
                recipient_node_raw = await self._graph_store.get_node(
                    intent.recipient_id, include_archived=False, caller_context=None
                )
                if recipient_node_raw:
                    prefs = recipient_node_raw.get("preferences", {}) or {}
                    preferred = prefs.get("preferred_channel", "email") or "email"
            except Exception as exc:  # noqa: BLE001
                logger.debug("OutboundCommunicationAgent: could not fetch recipient node: %s", exc)

        # Check stickiness: is there a recent CheckinNode on a different channel?
        active_thread = await self._get_active_sticky_thread(intent.recipient_id)
        if active_thread:
            sticky_channel, thread_id = active_thread
            if sticky_channel != preferred:
                logger.debug(
                    "OutboundCommunicationAgent: stickiness override %s→%s for recipient=%s",
                    preferred,
                    sticky_channel,
                    intent.recipient_id,
                )
                to_address = await self._get_channel_address(intent.recipient_id, sticky_channel)
                return sticky_channel, thread_id, to_address

        to_address = await self._get_channel_address(intent.recipient_id, preferred)
        return preferred, "", to_address

    async def _get_active_sticky_thread(self, recipient_id: str) -> tuple[str, str] | None:
        """Return (channel, thread_id) for most recent active CheckinNode within stickiness window."""
        if self._graph_store is None:
            return None
        try:
            from datetime import datetime, timedelta, timezone  # noqa: PLC0415

            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=self._default_stickiness_hours)
            ).isoformat()
            # Delegate to repository for stickiness lookup
            if hasattr(self._graph_store, "get_active_thread"):
                result = await self._graph_store.get_active_thread(
                    recipient_id=recipient_id,
                    since_iso=cutoff,
                )
                if result:
                    return result.get("channel"), result.get("thread_id")
        except Exception as exc:  # noqa: BLE001
            logger.debug("OutboundCommunicationAgent: stickiness lookup failed: %s", exc)
        return None

    async def _get_channel_address(self, recipient_id: str, channel: str) -> str:
        """Return the channel-specific address for *recipient_id*.

        Falls back to ``recipient_id`` itself when the graph is unavailable.
        """
        if self._graph_store is None:
            return recipient_id
        try:
            node_raw = await self._graph_store.get_node(
                recipient_id, include_archived=False, caller_context=None
            )
            if node_raw:
                identities = node_raw.get("identities", {}) or {}
                if channel == "email":
                    emails = identities.get("emails", [])
                    return emails[0] if emails else recipient_id
                elif channel == "telegram":
                    tid = identities.get("telegram_id", "")
                    return str(tid) if tid else recipient_id
                elif channel == "whatsapp":
                    wid = identities.get("whatsapp_id", "")
                    return str(wid) if wid else recipient_id
        except Exception as exc:  # noqa: BLE001
            logger.debug("OutboundCommunicationAgent: get_channel_address failed: %s", exc)
        return recipient_id

    async def _create_checkin(
        self,
        *,
        intent: Any,
        channel: str,
        thread_id: str,
        to_address: str,
    ) -> str | None:
        """Create a CheckinNode in the graph after dispatch."""
        if self._graph_store is None or intent.task_id is None:
            return None
        try:
            checkin_id = await self._graph_store.create_checkin_node(
                task_id=intent.task_id,
                outbound_message=intent.draft or intent.purpose,
                channel=channel,
                agent_id=self._agent_id or self._owner_user_id,
                recipient=intent.recipient_id,
            )
            logger.debug(
                "OutboundCommunicationAgent: CheckinNode created checkin_id=%s", checkin_id
            )
            return checkin_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutboundCommunicationAgent: create_checkin_node failed: %s", exc)
            return None

    async def _write_reply_key(
        self,
        *,
        intent: Any,
        channel: str,
        thread_id: str,
        checkin_id: str,
    ) -> None:
        """Write Redis + Postgres reply-key (FR-OUT-004)."""
        if self._reply_key_store is None:
            return
        from graphclaw.inbound.reply_keys import ReplyKeyRecord  # noqa: PLC0415

        record = ReplyKeyRecord(
            task_id=intent.task_id,
            counterparty_id=intent.recipient_id,
            user_id=self._owner_user_id,
            channel=channel,
            thread_id=thread_id or checkin_id,
            checkin_id=checkin_id,
        )
        await self._reply_key_store.write(record, msg_id=checkin_id)

    async def _append_intelligence(
        self,
        *,
        task_id: str,
        channel: str,
        recipient_id: str,
        purpose: str,
    ) -> None:
        """Append an intelligence line to the task node."""
        from datetime import datetime, timezone  # noqa: PLC0415

        now = datetime.now(timezone.utc).isoformat()
        line = f"[{now}] outbound/{channel} → {recipient_id}: {purpose}"
        try:
            await self._graph_store.update_node_intelligence(task_id, line)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutboundCommunicationAgent: update_node_intelligence failed: %s", exc)


__all__ = ["OutboundDispatcher", "OutboundCommunicationAgent", "DispatchResult"]
