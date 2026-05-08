# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.activity_formatter — Plain-language activity event formatter.

Converts structured log records into short human-readable messages for
Agent Monitor Activity feeds.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _read_str(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_int(record: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
    return None


def format_event(record: Mapping[str, Any]) -> str:
    """Return a plain-language summary for one structured event record."""
    event_type = _read_str(record, "event_type") or "event"

    if event_type == "task.scored":
        tasks_scored = _read_int(record, "tasks_scored", "tasksScored", "count")
        top_title = _read_str(record, "top_task_title", "topTaskTitle", "title")
        if tasks_scored is not None:
            if top_title:
                return f"Scored {tasks_scored} tasks - top priority: {top_title}"
            return f"Scored {tasks_scored} tasks."
        return "Task scoring cycle completed."

    if event_type == "skill.completed":
        skill_name = _read_str(record, "skill_name", "skillName") or "Skill"
        status = (_read_str(record, "status") or "").upper()
        failed = status in {"FAILED", "ERROR", "TIMEOUT"}
        if failed:
            return f"{skill_name} failed."
        return f"{skill_name} completed."

    if event_type == "briefing.ready":
        return "Daily briefing is ready."

    if event_type == "task.state_changed":
        to_state = _read_str(record, "to_state", "toState", "new_state", "newState")
        if to_state:
            return f"Task state changed to {to_state}."
        return "Task state changed."

    if event_type == "approval.pending":
        task_id = _read_str(record, "task_id", "taskId")
        if task_id:
            return f"Approval pending for task {task_id}."
        return "Approval is pending human review."

    if event_type == "inbound.processed":
        signal = _read_str(record, "signal")
        if signal:
            return f"Processed inbound update ({signal})."
        return "Processed inbound update."

    if event_type == "agent.message":
        return "Sent outbound agent message."

    if event_type == "outbound.sent":
        return "Outbound message sent."

    explicit_message = _read_str(record, "message", "summary")
    if explicit_message:
        return explicit_message

    return f"Event: {event_type}"
