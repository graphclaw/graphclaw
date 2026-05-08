# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.channels.email.config — Email channel configuration.

Description
-----------
Provides ``EmailConfig``, a Pydantic value object that collects all credentials
and tuning parameters required by both the ``EmailPoller`` (IMAP) and the
``EmailSender`` (SMTP) components.

Design Patterns
---------------
- DTO / Value Object: Immutable, schema-validated configuration container.
- Pydantic v2: Uses ``BaseModel`` with typed fields and sensible defaults.

Public API
----------
- EmailConfig: Configuration value object for email channel credentials.

Dependencies
------------
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

from pydantic import BaseModel


class EmailConfig(BaseModel):
    """Configuration for the email channel (IMAP + SMTP credentials).

    Attributes
    ----------
    imap_host:
        IMAP server hostname (e.g. ``"imap.gmail.com"``).  Empty string
        disables the IMAP poller.
    imap_port:
        IMAP server port.  Defaults to 993 (IMAP over TLS).
    smtp_host:
        SMTP server hostname (e.g. ``"smtp.gmail.com"``).
    smtp_port:
        SMTP server port.  Defaults to 587 (STARTTLS).
    username:
        Login username / email address for both IMAP and SMTP.
    password:
        App-specific password or OAuth token for IMAP/SMTP authentication.
    poll_interval:
        Seconds between IMAP poll cycles.  Defaults to 30.0.
    enabled:
        When ``False`` (the default), the email channel is disabled and neither
        the poller nor the sender will be started.
    """

    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    poll_interval: float = 30.0
    enabled: bool = False
