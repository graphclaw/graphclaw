"""Tests for configure_logging() / stop_logging() lifecycle."""

from __future__ import annotations

import io
import json
import logging
import time

import pytest

import graphclaw.infra.logging as log_pkg
from graphclaw.infra.logging import configure_logging, stop_logging
from graphclaw.infra.logging.formatter import JsonFormatter


def _reset_logging():
    """Reset module-level listener and remove test handlers."""
    stop_logging()
    gc_logger = logging.getLogger("graphclaw")
    gc_logger.handlers.clear()
    gc_logger.filters.clear()
    log_pkg._listener = None


@pytest.fixture(autouse=True)
def clean_logging():
    _reset_logging()
    yield
    _reset_logging()


class TestConfigureLogging:
    def test_configure_starts_listener(self):
        configure_logging(service_name="test-svc", log_level="DEBUG", sink_names=["stdout"])
        assert log_pkg._listener is not None

    def test_stop_clears_listener(self):
        configure_logging(service_name="test-svc", log_level="DEBUG", sink_names=["stdout"])
        stop_logging()
        assert log_pkg._listener is None

    def test_idempotent_configure(self):
        configure_logging(service_name="test-svc", log_level="DEBUG", sink_names=["stdout"])
        listener_before = log_pkg._listener
        configure_logging(service_name="test-svc", log_level="DEBUG", sink_names=["stdout"])
        assert log_pkg._listener is listener_before

    def test_log_record_reaches_handler(self):
        """Verify a log call after configure_logging reaches a handler."""
        captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                captured.append(record)

        cap = CapturingHandler()
        cap.setFormatter(JsonFormatter(service_name="test"))

        log_queue: "queue.Queue" = __import__("queue").Queue()
        from logging.handlers import QueueListener, QueueHandler
        from graphclaw.infra.logging.context import SessionFilter

        qh = QueueHandler(log_queue)
        gc_logger = logging.getLogger("graphclaw.test_configure")
        gc_logger.setLevel(logging.DEBUG)
        gc_logger.addHandler(qh)
        gc_logger.addFilter(SessionFilter())

        listener = QueueListener(log_queue, cap, respect_handler_level=True)
        listener.start()

        gc_logger.info("test.event", extra={"event_type": "test.event", "val": 42})
        time.sleep(0.1)  # allow listener thread to flush

        listener.stop()
        gc_logger.handlers.clear()
        gc_logger.filters.clear()

        assert len(captured) == 1
        line = cap.formatter.format(captured[0])
        doc = json.loads(line)
        assert doc["event_type"] == "test.event"
        assert doc["val"] == 42
        assert doc["service"] == "test"

    def test_session_filter_attached(self):
        configure_logging(service_name="svc", log_level="DEBUG", sink_names=["stdout"])
        gc_logger = logging.getLogger("graphclaw")
        filter_types = [type(f).__name__ for f in gc_logger.filters]
        assert "SessionFilter" in filter_types
