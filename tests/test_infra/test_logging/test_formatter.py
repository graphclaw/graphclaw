# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for JsonFormatter."""

from __future__ import annotations

import json
import logging

from graphclaw.infra.logging.formatter import JsonFormatter


def _make_record(
    name: str = "graphclaw.test",
    level: int = logging.INFO,
    msg: str = "test message",
    **extra,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestJsonFormatter:
    def setup_method(self):
        self.fmt = JsonFormatter(service_name="test-svc")

    def test_required_fields_present(self):
        record = _make_record()
        line = self.fmt.format(record)
        doc = json.loads(line)
        assert doc["level"] == "INFO"
        assert doc["service"] == "test-svc"
        assert doc["logger"] == "graphclaw.test"
        assert doc["message"] == "test message"
        assert doc["timestamp"].endswith("Z")

    def test_extra_fields_included(self):
        record = _make_record(event_type="agent.scoring", user_id="usr_1", latency_ms=42)
        doc = json.loads(self.fmt.format(record))
        assert doc["event_type"] == "agent.scoring"
        assert doc["user_id"] == "usr_1"
        assert doc["latency_ms"] == 42

    def test_session_id_included_when_set(self):
        record = _make_record(session_id="SES-abc")
        doc = json.loads(self.fmt.format(record))
        assert doc["session_id"] == "SES-abc"

    def test_empty_session_id_omitted(self):
        record = _make_record(session_id="")
        doc = json.loads(self.fmt.format(record))
        assert "session_id" not in doc

    def test_exc_info_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = _make_record()
        record.exc_info = exc_info
        doc = json.loads(self.fmt.format(record))
        assert "exc_info" in doc
        assert "ValueError" in doc["exc_info"]

    def test_standard_logrecord_keys_excluded(self):
        record = _make_record()
        doc = json.loads(self.fmt.format(record))
        for key in ("lineno", "pathname", "thread", "processName"):
            assert key not in doc

    def test_level_names(self):
        for level, name in [
            (logging.DEBUG, "DEBUG"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
        ]:
            record = _make_record(level=level)
            doc = json.loads(self.fmt.format(record))
            assert doc["level"] == name

    def test_output_is_valid_json(self):
        record = _make_record(event_type="x", data={"nested": [1, 2, 3]})
        line = self.fmt.format(record)
        doc = json.loads(line)
        assert isinstance(doc, dict)
