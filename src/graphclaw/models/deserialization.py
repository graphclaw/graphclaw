"""Shared helpers for decoding AGE task node property payloads.

AGE stores nested Pydantic fields as JSON strings in node properties.
These helpers decode those fields so TaskNode validation can run against
native Python dict/list structures.
"""

from __future__ import annotations

import json
from typing import Any

TASK_NODE_JSON_STR_FIELDS = (
    "scoring",
    "timeline",
    "progress",
    "override",
    "autonomy",
    "type_metadata",
)
TASK_NODE_JSON_LIST_FIELDS = ("state_history", "update_log", "tags")


def deserialize_task_node_props(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON-string TaskNode fields from a raw AGE node payload."""
    result: dict[str, Any] = dict(raw)

    for field in TASK_NODE_JSON_STR_FIELDS:
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, ValueError):
                result[field] = None

    for field in TASK_NODE_JSON_LIST_FIELDS:
        value = result.get(field)
        if isinstance(value, str):
            try:
                result[field] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[field] = []
            continue

        if isinstance(value, list):
            parsed_items: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    try:
                        parsed_items.append(json.loads(item))
                    except (json.JSONDecodeError, ValueError):
                        parsed_items.append(item)
                else:
                    parsed_items.append(item)
            result[field] = parsed_items

    return result


__all__ = [
    "TASK_NODE_JSON_LIST_FIELDS",
    "TASK_NODE_JSON_STR_FIELDS",
    "deserialize_task_node_props",
]
