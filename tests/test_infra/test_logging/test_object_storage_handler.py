# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
GC-U-INF-W50-001 - validates object storage log handler path partitioning and batching.

Scenario: ObjectStorageHandler should batch writes correctly and generate
race-safe per-process object keys for hourly NDJSON partitions.

PRD: docs/graphclaw-requirements.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-05

Cases covered:
- user and system log paths include the expected logs prefix and extension
- generated path includes per-process suffix to avoid key collisions
- handler batch and close flow call flush as expected
- append merge behavior preserves existing NDJSON lines
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, patch

from graphclaw.infra.logging.formatter import JsonFormatter
from graphclaw.infra.logging.handlers.object_storage import ObjectStorageHandler


def _make_record(level: int = logging.INFO, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="graphclaw.test",
        level=level,
        pathname="",
        lineno=0,
        msg="test",
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestObjectStorageHandler:
    def _make_handler(self, batch_size: int = 5, flush_interval: float = 3600.0):
        h = ObjectStorageHandler(
            bucket="test-bucket",
            endpoint_url="http://localhost:9000",
            region="us-east-1",
            min_level="INFO",
            batch_size=batch_size,
            flush_interval=flush_interval,
        )
        h.setFormatter(JsonFormatter(service_name="test"))
        return h

    def test_compute_path_user_scoped(self):
        h = self._make_handler()
        record = _make_record(user_id="usr_abc", service="gateway")
        path = h._compute_path(record)
        assert path.startswith("usr_abc/logs/gateway/")
        assert path.endswith(".jsonl")

    def test_compute_path_system_fallback(self):
        h = self._make_handler()
        record = _make_record(service="gateway")
        path = h._compute_path(record)
        assert path.startswith("system/logs/gateway/")

    def test_min_level_filters_debug(self):
        h = self._make_handler()
        debug_record = _make_record(level=logging.DEBUG)
        with patch.object(h, "_flush") as mock_flush:
            h.emit(debug_record)
            mock_flush.assert_not_called()

    def test_batch_triggers_flush(self):
        h = self._make_handler(batch_size=3)
        with patch.object(h, "_flush") as mock_flush:
            for _ in range(3):
                h.emit(_make_record())
            mock_flush.assert_called_once()

    def test_close_calls_flush(self):
        h = self._make_handler()
        h.emit(_make_record())
        with patch.object(h, "_flush") as mock_flush:
            h.close()
            mock_flush.assert_called_once()

    def test_path_contains_hour_partition(self):
        h = self._make_handler()
        record = _make_record(user_id="u1", service="gw")
        path = h._compute_path(record)
        # Format: u1/logs/gw/YYYY-MM-DD/HH00Z-{pid}-{suffix}.jsonl
        parts = path.split("/")
        assert len(parts) == 5
        assert re.match(r"^\d{2}00Z-\d+-[0-9a-f]{6}\.jsonl$", parts[4])

    def test_path_suffix_is_stable_per_handler_instance(self):
        h = self._make_handler()
        record_a = _make_record(user_id="u1", service="gw")
        record_b = _make_record(user_id="u1", service="gw")

        path_a = h._compute_path(record_a)
        path_b = h._compute_path(record_b)

        assert path_a == path_b

    def test_append_to_s3_merges_existing(self):
        h = self._make_handler()
        mock_client = MagicMock()
        mock_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b'{"existing":"line"}\n'))
        }
        h._append_to_s3(mock_client, "path/test.jsonl", ['{"new":"line"}'])
        call_kwargs = mock_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        assert '{"existing":"line"}' in body
        assert '{"new":"line"}' in body
