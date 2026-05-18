# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.logging.formatter — JsonFormatter for structured JSONL output.

Serializes every LogRecord to a single JSONL line. All extra={} keys passed to
logger.info/debug/etc. are included in the output alongside the standard fields.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_STANDARD_LOG_RECORD_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """Serializes every LogRecord to a single compact JSONL line.

    Fields always present:
        timestamp  — ISO-8601 UTC with Z suffix
        level      — DEBUG | INFO | WARNING | ERROR | CRITICAL
        service    — configured service name
        logger     — record.name (Python logger hierarchy name)
        message    — record.getMessage()

    Fields present when set via extra={}:
        event_type  — business event classifier (e.g. "agent.scoring_cycle")
        session_id  — injected by SessionFilter from ContextVar
        user_id, task_id, agent_id, latency_ms, ... — any extra key

    Fields present on exceptions:
        exc_info    — formatted traceback string
    """

    def __init__(self, service_name: str = "graphclaw") -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

        doc: dict = {
            "timestamp": ts_str,
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_KEYS and not key.startswith("_"):
                doc[key] = value

        if record.exc_info:
            doc["exc_info"] = self.formatException(record.exc_info)

        # Drop empty session_id to keep output clean
        if doc.get("session_id") == "":
            del doc["session_id"]

        return json.dumps(doc, default=str, separators=(",", ":"))
