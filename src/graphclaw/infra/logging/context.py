# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.logging.context — session_id ContextVar and SessionFilter.

session_id is set once per request/task entry point via set_session_id() and
propagated automatically to all downstream log calls via SessionFilter.

asyncio contextvars propagates the value to all coroutines spawned within the
same request context — no parameter threading required.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_session_id_var: ContextVar[str] = ContextVar("session_id", default="")


def set_session_id(session_id: str) -> None:
    """Set the session_id for the current async context."""
    _session_id_var.set(session_id)


def get_session_id() -> str:
    """Read the session_id from the current context; returns '' when not set."""
    return _session_id_var.get()


def generate_session_id() -> str:
    """Generate a new SES-{uuid4} session identifier."""
    return f"SES-{uuid.uuid4()}"


class SessionFilter(logging.Filter):
    """Injects session_id from ContextVar into every LogRecord.

    Attached to the graphclaw logger so all log records carry the current
    session_id. Callers that pass session_id explicitly via extra={} keep
    their value — this filter only fills in the blank.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "session_id", ""):
            record.session_id = _session_id_var.get()
        return True
