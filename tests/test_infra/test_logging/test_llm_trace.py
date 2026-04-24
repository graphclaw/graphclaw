"""Tests for LLM trace logger configuration and isolation."""

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest

import graphclaw.infra.logging.llm_trace as trace_mod
from graphclaw.infra.logging.llm_trace import (
    configure_llm_trace_logger,
    get_llm_trace_logger,
)


@pytest.fixture(autouse=True)
def reset_trace_logger():
    yield
    # Reset after each test
    logger = logging.getLogger("graphclaw.llm.trace")
    logger.handlers.clear()
    trace_mod._llm_trace_logger = None


class TestLLMTraceLogger:
    def test_disabled_by_default(self):
        configure_llm_trace_logger(enabled=False)
        assert get_llm_trace_logger() is None

    def test_enabled_creates_logger(self, tmp_path):
        log_path = str(tmp_path / "llm-traces.jsonl")
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        assert get_llm_trace_logger() is not None

    def test_propagate_is_false(self, tmp_path):
        log_path = str(tmp_path / "llm-traces.jsonl")
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        trace_logger = get_llm_trace_logger()
        assert trace_logger.propagate is False

    def test_writes_to_file(self, tmp_path):
        log_path = str(tmp_path / "llm-traces.jsonl")
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        trace_logger = get_llm_trace_logger()
        trace_logger.info(
            "llm.trace",
            extra={
                "event_type": "llm.trace",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        )
        # Flush handlers
        for h in trace_logger.handlers:
            h.flush()

        content = open(log_path).read()
        assert content.strip()
        doc = json.loads(content.strip())
        assert doc["event_type"] == "llm.trace"
        assert doc["provider"] == "anthropic"

    def test_idempotent_configure(self, tmp_path):
        log_path = str(tmp_path / "llm-traces.jsonl")
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        logger_first = get_llm_trace_logger()
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        logger_second = get_llm_trace_logger()
        assert logger_first is logger_second

    def test_trace_does_not_leak_to_root(self, tmp_path, caplog):
        log_path = str(tmp_path / "llm-traces.jsonl")
        configure_llm_trace_logger(enabled=True, log_path=log_path)
        trace_logger = get_llm_trace_logger()
        with caplog.at_level(logging.DEBUG, logger="graphclaw.llm.trace"):
            trace_logger.info("llm.trace", extra={"event_type": "llm.trace"})
        # propagate=False means it should NOT appear in caplog's root records
        assert not any(r.name == "graphclaw.llm.trace" for r in caplog.records)
