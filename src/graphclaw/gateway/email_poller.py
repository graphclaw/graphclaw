"""graphclaw.gateway.email_poller — Shim re-exporting from channels.email.poller.

Description
-----------
Backward-compatibility shim. The canonical implementation has moved to
``graphclaw.gateway.channels.email.poller``. This module re-exports
``EmailPoller`` so that existing imports continue to work without change.

Design Patterns
---------------
- Shim / Facade: Thin re-export layer preserving the original public API.

Public API
----------
- EmailPoller: Background IMAP polling loop.

Dependencies
------------
- graphclaw.gateway.channels.email.poller: EmailPoller.
"""
from __future__ import annotations

from graphclaw.gateway.channels.email.poller import EmailPoller

__all__ = ["EmailPoller"]
