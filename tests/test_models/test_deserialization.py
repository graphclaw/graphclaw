"""Tests for shared AGE TaskNode deserialization helpers."""

from __future__ import annotations

from graphclaw.models.deserialization import deserialize_task_node_props


def test_deserialize_task_node_props_parses_json_fields() -> None:
    raw = {
        "scoring": '{"computed_priority": 0.73}',
        "timeline": '{"deadline": "2026-05-01T00:00:00+00:00"}',
        "state_history": '[{"from_state": "PENDING", "to_state": "ACTIVE"}]',
        "tags": '["alpha", "beta"]',
    }

    parsed = deserialize_task_node_props(raw)

    assert parsed["scoring"]["computed_priority"] == 0.73
    assert parsed["timeline"]["deadline"].startswith("2026-05-01")
    assert isinstance(parsed["state_history"], list)
    assert parsed["state_history"][0]["to_state"] == "ACTIVE"
    assert parsed["tags"] == ["alpha", "beta"]


def test_deserialize_task_node_props_handles_invalid_json_gracefully() -> None:
    raw = {
        "scoring": "not-json",
        "state_history": "not-json",
        "update_log": ['{"event": "ok"}', "not-json"],
    }

    parsed = deserialize_task_node_props(raw)

    assert parsed["scoring"] is None
    assert parsed["state_history"] == []
    assert isinstance(parsed["update_log"], list)
    assert parsed["update_log"][0]["event"] == "ok"
    assert parsed["update_log"][1] == "not-json"
