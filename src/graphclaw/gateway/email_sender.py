"""graphclaw.gateway.email_sender — Shim re-exporting from channels.email.sender.

Description
-----------
Backward-compatibility shim. The canonical implementation has moved to
``graphclaw.gateway.channels.email.sender``. This module re-exports
``EmailSender`` so that existing imports continue to work without change.

Design Patterns
---------------
- Shim / Facade: Thin re-export layer preserving the original public API.

Public API
----------
- EmailSender: SMTP outbound sender and queue consumer.

Dependencies
------------
- graphclaw.gateway.channels.email.sender: EmailSender.
"""
from __future__ import annotations

from graphclaw.gateway.channels.email.sender import EmailSender

__all__ = ["EmailSender"]
