"""graphclaw.gateway.email — Shim re-exporting email channel components.

Description
-----------
Backward-compatibility shim. The canonical implementations have moved to
``graphclaw.gateway.channels.email``. This module re-exports the primary
email channel components so that existing imports continue to work without
change.

The ``send_email`` free function is retained here as a backward-compat wrapper
around ``EmailSender.send``; it accepts an ``OutboundMessage`` and an
``EmailConfig`` to match the original signature.

Design Patterns
---------------
- Shim / Facade: Thin re-export layer preserving the original public API.

Public API
----------
- EmailConfig: Configuration value object for email channel credentials.
- EmailPoller: Background IMAP polling loop.
- EmailSender: SMTP outbound sender and queue consumer.
- send_email: Send a single outbound email (backward-compat free function).

Dependencies
------------
- graphclaw.gateway.channels.email.config: EmailConfig.
- graphclaw.gateway.channels.email.poller: EmailPoller.
- graphclaw.gateway.channels.email.sender: EmailSender.
- graphclaw.gateway.schemas: OutboundMessage.
"""
from __future__ import annotations

from graphclaw.gateway.channels.email.config import EmailConfig
from graphclaw.gateway.channels.email.poller import EmailPoller
from graphclaw.gateway.channels.email.sender import EmailSender
from graphclaw.gateway.schemas import OutboundMessage


async def send_email(
    message: OutboundMessage,
    config: EmailConfig,
) -> None:
    """Send a single outbound email via SMTP.

    Backward-compatibility wrapper around ``EmailSender.send``.

    Parameters
    ----------
    message:
        ``OutboundMessage`` to deliver.  Only ``channel="email"`` messages
        are handled; others raise ``ValueError``.
    config:
        ``EmailConfig`` containing SMTP host, port, and credentials.

    Raises
    ------
    ValueError
        If ``message.channel`` is not ``"email"``.
    """
    if message.channel != "email":
        raise ValueError(
            f"send_email only handles channel='email', got {message.channel!r}"
        )
    sender = EmailSender(
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.username,
        password=config.password,
        use_tls=(config.smtp_port == 465),
    )
    await sender.send(
        recipient=message.recipient,
        subject=message.subject,
        body=message.body,
        in_reply_to=message.in_reply_to,
    )


__all__ = ["EmailConfig", "EmailPoller", "EmailSender", "send_email"]
