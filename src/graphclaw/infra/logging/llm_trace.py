# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.logging.llm_trace — Isolated LLM prompt/completion trace logger.

The "graphclaw.llm.trace" logger is completely isolated from the main logging
hierarchy (propagate=False). Full prompts and responses are logged only to a
local rotating file — never to stdout, S3, or CloudWatch.

Activation: LLM_TRACE=true env var OR LOG_LEVEL=DEBUG.

WARNING: This file contains full user message content (PII). It must be in
.gitignore and must NOT be included in any log shipping pipeline without
explicit opt-in.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LLM_TRACE_LOGGER_NAME = "graphclaw.llm.trace"
_llm_trace_logger: logging.Logger | None = None

_50_MB = 50 * 1024 * 1024


def configure_llm_trace_logger(
    enabled: bool,
    log_dir: str = "logs",
    log_path: str | None = None,
) -> None:
    """Configure the LLM trace logger if enabled.

    Args:
        enabled: Whether to activate LLM tracing.
        log_dir: Directory for the trace file (used when log_path is None).
        log_path: Full path for the trace file (overrides log_dir).
    """
    global _llm_trace_logger
    if not enabled:
        return

    trace_path = log_path or f"{log_dir}/llm-traces.jsonl"
    Path(trace_path).parent.mkdir(parents=True, exist_ok=True)

    trace_logger = logging.getLogger(_LLM_TRACE_LOGGER_NAME)
    trace_logger.setLevel(logging.DEBUG)
    trace_logger.propagate = False  # MUST stay False — content must never reach stdout

    if trace_logger.handlers:
        return  # Already configured (idempotent)

    handler = logging.handlers.RotatingFileHandler(
        filename=trace_path,
        maxBytes=_50_MB,
        backupCount=10,
        encoding="utf-8",
    )
    from graphclaw.infra.logging.formatter import JsonFormatter

    handler.setFormatter(JsonFormatter(service_name="llm-trace"))
    trace_logger.addHandler(handler)
    _llm_trace_logger = trace_logger


def get_llm_trace_logger() -> logging.Logger | None:
    """Return the LLM trace logger if configured, else None."""
    return _llm_trace_logger
