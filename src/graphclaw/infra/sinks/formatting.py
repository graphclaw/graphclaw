"""Shared log-entry formatting helpers for sink implementations."""

from __future__ import annotations

import json
from typing import Any

_PIPE_COLUMNS = (
    "timestamp",
    "level",
    "service",
    "event_type",
    "session_id",
    "user_id",
    "task_id",
)


def _normalize(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        text = value.strip()
        return text if text else "-"
    return str(value)


def _escape_pipe(text: str) -> str:
    return text.replace("|", r"\|")


def format_pipe_entry(entry: dict[str, Any]) -> str:
    """Serialize one entry into fixed-column pipe-delimited format."""
    values = [_escape_pipe(_normalize(entry.get(column))) for column in _PIPE_COLUMNS]

    extra = {k: v for k, v in entry.items() if k not in _PIPE_COLUMNS}
    if extra:
        extra_json = json.dumps(extra, separators=(",", ":"), default=str)
    else:
        extra_json = "-"

    values.append(_escape_pipe(extra_json))
    return "|".join(values)


def format_jsonl_entry(entry: dict[str, Any]) -> str:
    """Serialize one entry into compact JSONL format."""
    return json.dumps(entry, default=str, separators=(",", ":"))


def format_entry(entry: dict[str, Any], log_format: str) -> str:
    """Serialize one entry using the selected line format."""
    if log_format == "pipe":
        return format_pipe_entry(entry)
    return format_jsonl_entry(entry)
