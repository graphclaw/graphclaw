"""graphclaw.infra.logging — Unified stdlib-based structured logging system.

Replaces the custom AsyncLogger with stdlib QueueHandler + QueueListener.
QueueListener runs in a dedicated OS thread — immune to asyncio event loop
congestion. All 94+ existing logging.getLogger(__name__) call sites need no
changes.

Public API:
    configure_logging()   — build handlers, start QueueListener (call once at startup)
    stop_logging()        — drain queue and stop QueueListener (call at shutdown)
    set_session_id()      — set session_id ContextVar for current async context
    get_session_id()      — read session_id from current context
    generate_session_id() — generate SES-{uuid4} identifier
"""

from __future__ import annotations

import logging
import logging.handlers
import queue

from graphclaw.infra.logging.context import (
    SessionFilter,
    generate_session_id,
    get_session_id,
    set_session_id,
)
from graphclaw.infra.logging.formatter import JsonFormatter
from graphclaw.infra.logging.llm_trace import configure_llm_trace_logger

__all__ = [
    "configure_logging",
    "stop_logging",
    "set_session_id",
    "get_session_id",
    "generate_session_id",
    "JsonFormatter",
    "SessionFilter",
]

_listener: logging.handlers.QueueListener | None = None


class _DroppingQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that drops records silently when the queue is full.

    The default QueueHandler blocks when the queue is full. This subclass
    uses put_nowait() and swallows queue.Full to match the old AsyncLogger
    behaviour of never blocking the caller.
    """

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass


def configure_logging(
    service_name: str = "gateway",
    log_level: str = "INFO",
    sink_names: list[str] | None = None,
    storage_bucket: str = "graphclaw",
    storage_endpoint_url: str | None = None,
    storage_region: str = "us-east-1",
    cloudwatch_region: str = "us-east-1",
    cloudwatch_log_group_prefix: str = "/graphclaw",
    llm_trace_enabled: bool = False,
    llm_trace_path: str | None = None,
    queue_maxsize: int = 10_000,
) -> None:
    """Configure the unified stdlib logging system.

    Builds handlers from sink_names, wraps them in a QueueListener running in
    a dedicated OS thread, and attaches SessionFilter to the graphclaw logger.

    Call once during application startup (e.g. in lifespan / init_services).
    Subsequent calls are no-ops (idempotent).

    Args:
        service_name: Embedded in every log record as the "service" field.
        log_level: Minimum log level string (DEBUG/INFO/WARNING/ERROR).
        sink_names: List of sink names: "stdout", "object_storage", "cloudwatch".
        storage_bucket: S3/MinIO bucket for object_storage sink.
        storage_endpoint_url: MinIO endpoint URL (None for AWS S3).
        storage_region: AWS/MinIO region.
        cloudwatch_region: AWS region for CloudWatch sink.
        cloudwatch_log_group_prefix: CloudWatch log group prefix.
        llm_trace_enabled: Whether to activate LLM prompt/response tracing.
        llm_trace_path: Override path for llm-traces.jsonl.
        queue_maxsize: Maximum records in queue before dropping (default 10_000).
    """
    global _listener
    if _listener is not None:
        return  # Already configured

    if sink_names is None:
        sink_names = ["stdout"]

    level_no = getattr(logging, log_level.upper(), logging.INFO)
    formatter = JsonFormatter(service_name=service_name)

    handlers: list[logging.Handler] = []

    if "stdout" in sink_names:
        from graphclaw.infra.logging.handlers.stdout import StdoutJsonHandler

        h = StdoutJsonHandler(min_level=log_level)
        h.setFormatter(formatter)
        handlers.append(h)

    if "object_storage" in sink_names:
        from graphclaw.infra.logging.handlers.object_storage import ObjectStorageHandler

        h = ObjectStorageHandler(
            bucket=storage_bucket,
            endpoint_url=storage_endpoint_url,
            region=storage_region,
            min_level=log_level,
        )
        h.setFormatter(formatter)
        handlers.append(h)

    if "cloudwatch" in sink_names:
        from graphclaw.infra.logging.handlers.cloudwatch import build_cloudwatch_handler

        h = build_cloudwatch_handler(
            region=cloudwatch_region,
            log_group_prefix=cloudwatch_log_group_prefix,
            service_name=service_name,
            min_level="WARNING",
        )
        if not isinstance(h, logging.NullHandler):
            h.setFormatter(formatter)
        handlers.append(h)

    if not handlers:
        from graphclaw.infra.logging.handlers.stdout import StdoutJsonHandler

        h = StdoutJsonHandler(min_level=log_level)
        h.setFormatter(formatter)
        handlers.append(h)

    log_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
    queue_handler = _DroppingQueueHandler(log_queue)

    # Attach to graphclaw logger — captures all graphclaw.* loggers
    graphclaw_logger = logging.getLogger("graphclaw")
    graphclaw_logger.setLevel(level_no)
    graphclaw_logger.addHandler(queue_handler)
    graphclaw_logger.addFilter(SessionFilter())

    # Set root logger level so propagation works correctly
    logging.getLogger().setLevel(level_no)

    _listener = logging.handlers.QueueListener(
        log_queue,
        *handlers,
        respect_handler_level=True,
    )
    _listener.start()

    configure_llm_trace_logger(
        enabled=llm_trace_enabled,
        log_path=llm_trace_path,
    )


def stop_logging() -> None:
    """Stop the QueueListener, draining the queue before exit."""
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
