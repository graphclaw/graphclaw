# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.run_events.

Covers model construction, serialization, monotonic event_seq ordering,
terminal event guarantees, and payload sanitization.

These tests are pure unit tests — no external services required.
Run with::

    pytest tests/test_agent/test_run_events.py
"""

from __future__ import annotations

import re

import pytest

from graphclaw.agent.run_events import (
    SCHEMA_VERSION,
    AssistantDeltaPayload,
    AssistantFinalPayload,
    RunCompletedPayload,
    RunEventType,
    RunFailedPayload,
    RunStartedPayload,
    ToolCompletedPayload,
    ToolFailedPayload,
    ToolStartedPayload,
    make_event,
    new_run_id,
    sanitize_args,
    sanitize_text,
)

# ---------------------------------------------------------------------------
# sanitize helpers
# ---------------------------------------------------------------------------


class TestSanitizeText:
    def test_passthrough_short(self):
        assert sanitize_text("hello", 10) == "hello"

    def test_truncates_long(self):
        result = sanitize_text("a" * 100, 10)
        assert len(result) == 11  # 10 + ellipsis char
        assert result.endswith("…")

    def test_exactly_at_limit(self):
        result = sanitize_text("abc", 3)
        assert result == "abc"


class TestSanitizeArgs:
    def test_strips_secret_keys(self):
        args = {"task_id": "t1", "api_key": "sk-secret", "password": "hunter2", "title": "ok"}
        result = sanitize_args(args, max_len=500)
        assert "[redacted]" in result
        assert "sk-secret" not in result
        assert "hunter2" not in result
        assert "t1" in result
        assert "ok" in result

    def test_strips_token_key(self):
        result = sanitize_args({"access_token": "abc123"}, max_len=500)
        assert "abc123" not in result
        assert "[redacted]" in result

    def test_truncates(self):
        args = {"key": "x" * 300}
        result = sanitize_args(args)  # default max 200
        assert len(result) <= 201  # 200 + ellipsis

    def test_empty_args(self):
        assert sanitize_args({}) == "{}"


# ---------------------------------------------------------------------------
# make_event factory
# ---------------------------------------------------------------------------


class TestMakeEvent:
    def test_creates_event_with_correct_fields(self):
        run_id = new_run_id()
        event = make_event(
            RunEventType.RUN_STARTED,
            run_id,
            "ses-001",
            "user-001",
            0,
            RunStartedPayload(message_preview="hello"),
        )
        assert event.run_id == run_id
        assert event.session_id == "ses-001"
        assert event.user_id == "user-001"
        assert event.event_seq == 0
        assert event.event_type == RunEventType.RUN_STARTED
        # Timestamp is a valid ISO-8601 string
        assert "T" in event.timestamp

    def test_payload_type_preserved(self):
        event = make_event(
            RunEventType.RUN_COMPLETED,
            new_run_id(),
            "",
            "u",
            1,
            RunCompletedPayload(
                input_tokens=100,
                output_tokens=50,
                tool_call_count=2,
                duration_ms=1500,
            ),
        )
        payload = event.payload
        assert isinstance(payload, RunCompletedPayload)
        assert payload.input_tokens == 100
        assert payload.duration_ms == 1500

    def test_serializes_to_json(self):
        event = make_event(
            RunEventType.ASSISTANT_DELTA,
            new_run_id(),
            "ses",
            "usr",
            5,
            AssistantDeltaPayload(delta="Hello"),
        )
        d = event.model_dump(mode="json")
        assert d["event_type"] == "assistant.delta"
        assert d["event_seq"] == 5
        # payload is serialized as a dict
        assert isinstance(d["payload"], dict)

    def test_schema_version_in_payload(self):
        event = make_event(
            RunEventType.TOOL_STARTED,
            new_run_id(),
            "",
            "u",
            0,
            ToolStartedPayload(tool_name="list_tasks", args_summary="user_id=u1"),
        )
        assert event.payload.schema_version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# monotonic event_seq
# ---------------------------------------------------------------------------


class TestEventSeqOrdering:
    def test_seq_increments_across_events(self):
        run_id = new_run_id()
        events = []
        for i in range(5):
            events.append(
                make_event(
                    RunEventType.ASSISTANT_DELTA,
                    run_id,
                    "s",
                    "u",
                    i,
                    AssistantDeltaPayload(delta=f"chunk-{i}"),
                )
            )
        seqs = [e.event_seq for e in events]
        assert seqs == list(range(5))

    def test_terminal_events_have_highest_seq(self):
        """run.completed must have a higher seq than all preceding events."""
        run_id = new_run_id()
        preceding = [
            make_event(RunEventType.RUN_STARTED, run_id, "", "u", 0, RunStartedPayload()),
            make_event(
                RunEventType.ASSISTANT_DELTA, run_id, "", "u", 1, AssistantDeltaPayload(delta="hi")
            ),
            make_event(
                RunEventType.ASSISTANT_FINAL,
                run_id,
                "",
                "u",
                2,
                AssistantFinalPayload(content_length=2),
            ),
        ]
        terminal = make_event(
            RunEventType.RUN_COMPLETED,
            run_id,
            "",
            "u",
            3,
            RunCompletedPayload(
                input_tokens=10, output_tokens=5, tool_call_count=0, duration_ms=100
            ),
        )
        assert terminal.event_seq > max(e.event_seq for e in preceding)


# ---------------------------------------------------------------------------
# Terminal event types
# ---------------------------------------------------------------------------


class TestTerminalEvents:
    def test_run_completed_is_terminal(self):
        assert RunEventType.RUN_COMPLETED in {
            RunEventType.RUN_COMPLETED,
            RunEventType.RUN_FAILED,
        }

    def test_run_failed_is_terminal(self):
        assert RunEventType.RUN_FAILED in {
            RunEventType.RUN_COMPLETED,
            RunEventType.RUN_FAILED,
        }

    def test_assistant_delta_is_not_terminal(self):
        assert RunEventType.ASSISTANT_DELTA not in {
            RunEventType.RUN_COMPLETED,
            RunEventType.RUN_FAILED,
        }


# ---------------------------------------------------------------------------
# Payload field validation
# ---------------------------------------------------------------------------


class TestPayloadModels:
    def test_run_failed_requires_error_class(self):
        with pytest.raises(Exception):
            RunFailedPayload()  # missing required error_class

    def test_tool_completed_has_defaults(self):
        p = ToolCompletedPayload(tool_name="t", latency_ms=100)
        assert p.result_summary == ""

    def test_tool_failed_requires_error_fields(self):
        with pytest.raises(Exception):
            ToolFailedPayload()  # missing required fields

    def test_assistant_delta_requires_delta(self):
        with pytest.raises(Exception):
            AssistantDeltaPayload()  # delta has no default

    def test_run_started_preview_empty_by_default(self):
        p = RunStartedPayload()
        assert p.message_preview == ""

    def test_new_run_id_is_uuid_format(self):
        rid = new_run_id()
        assert re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", rid), f"Not UUID format: {rid}"
