"""graphclaw.gateway.normalizer — Shim re-exporting from channels.email.normalizer.

Description
-----------
Backward-compatibility shim. The canonical implementation has moved to
``graphclaw.gateway.channels.email.normalizer``. This module re-exports
``normalize_email`` so that existing imports continue to work without change.

Design Patterns
---------------
- Shim / Facade: Thin re-export layer preserving the original public API.

Public API
----------
- normalize_email: Convert ``email.message.EmailMessage`` to ``InboundMessage``.

Dependencies
------------
- graphclaw.gateway.channels.email.normalizer: normalize_email.
"""
from __future__ import annotations

from graphclaw.gateway.channels.email.normalizer import normalize_email

__all__ = ["normalize_email"]
