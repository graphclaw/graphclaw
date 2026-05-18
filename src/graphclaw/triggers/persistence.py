# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.triggers.persistence — DB-backed trigger settings persistence helpers.

Description
-----------
Centralizes read/write logic for trigger policy settings and trigger schedules
stored on ``UserNode.preferences``. This module keeps API and gateway startup
paths consistent and provides a safe migration target away from object-storage-
backed schedule state.

Storage schema
--------------
- preferences.default_follow_up_days: int
- preferences.interrupt_threshold_overrides: dict[str, float]
- preferences.trigger_schedules: list[TriggerConfig JSON dict]

Notes
-----
``GraphStore`` implementations used by API/admin code may accept an optional
``caller_context`` kwarg while test doubles often do not. Helper functions here
gracefully retry without caller_context on ``TypeError``.
"""

from __future__ import annotations

import json
from typing import Any

from graphclaw.triggers.models import TriggerConfig

DEFAULT_FOLLOW_UP_DAYS = 3
TRIGGER_SCHEDULES_KEY = "trigger_schedules"


class TriggerPersistenceError(RuntimeError):
    """Raised when trigger settings cannot be persisted."""


class UserNodeNotFoundError(TriggerPersistenceError):
    """Raised when the target user node does not exist."""


async def _get_user_node(store: Any, user_id: str, caller_context: Any | None = None) -> dict | None:
    if caller_context is None:
        return await store.get_node(user_id)
    try:
        return await store.get_node(user_id, caller_context=caller_context)
    except TypeError:
        return await store.get_node(user_id)


async def _update_user_node(
    store: Any,
    user_id: str,
    updates: dict[str, Any],
    caller_context: Any | None = None,
) -> dict | None:
    if caller_context is None:
        return await store.update_node(user_id, updates)
    try:
        return await store.update_node(user_id, updates, caller_context=caller_context)
    except TypeError:
        return await store.update_node(user_id, updates)


def _normalise_preferences(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _normalise_overrides(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, float] = {}
    for key, value in raw.items():
        try:
            clean[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return clean


async def load_follow_up_settings(
    store: Any,
    user_id: str,
    caller_context: Any | None = None,
) -> dict[str, Any]:
    """Load follow-up policy settings from UserNode.preferences."""
    node = await _get_user_node(store, user_id, caller_context=caller_context)
    if node is None:
        raise UserNodeNotFoundError(f"User not found: {user_id}")

    preferences = _normalise_preferences(node.get("preferences"))
    default_follow_up_days = preferences.get("default_follow_up_days", DEFAULT_FOLLOW_UP_DAYS)
    try:
        default_follow_up_days = int(default_follow_up_days)
    except (TypeError, ValueError):
        default_follow_up_days = DEFAULT_FOLLOW_UP_DAYS

    return {
        "default_follow_up_days": default_follow_up_days,
        "interrupt_threshold_overrides": _normalise_overrides(
            preferences.get("interrupt_threshold_overrides", {})
        ),
    }


async def save_follow_up_settings(
    store: Any,
    user_id: str,
    default_follow_up_days: int | None = None,
    interrupt_threshold_overrides: dict[str, float] | None = None,
    caller_context: Any | None = None,
) -> dict[str, Any]:
    """Persist follow-up policy settings on UserNode.preferences."""
    node = await _get_user_node(store, user_id, caller_context=caller_context)
    if node is None:
        raise UserNodeNotFoundError(f"User not found: {user_id}")

    preferences = _normalise_preferences(node.get("preferences"))

    if default_follow_up_days is not None:
        preferences["default_follow_up_days"] = int(default_follow_up_days)

    if interrupt_threshold_overrides is not None:
        preferences["interrupt_threshold_overrides"] = _normalise_overrides(
            interrupt_threshold_overrides
        )

    updated = await _update_user_node(
        store,
        user_id,
        {"preferences": preferences},
        caller_context=caller_context,
    )
    if updated is None:
        raise TriggerPersistenceError(f"Failed to persist follow-up settings for {user_id}")

    return {
        "default_follow_up_days": int(
            preferences.get("default_follow_up_days", DEFAULT_FOLLOW_UP_DAYS)
        ),
        "interrupt_threshold_overrides": _normalise_overrides(
            preferences.get("interrupt_threshold_overrides", {})
        ),
    }


async def load_trigger_schedule(
    store: Any,
    user_id: str,
    *,
    agent_id: str | None = None,
    caller_context: Any | None = None,
) -> list[TriggerConfig]:
    """Load persisted trigger schedule from UserNode.preferences."""
    node = await _get_user_node(store, user_id, caller_context=caller_context)
    if node is None:
        return []

    preferences = _normalise_preferences(node.get("preferences"))
    raw_schedule = preferences.get(TRIGGER_SCHEDULES_KEY, [])
    if not isinstance(raw_schedule, list):
        return []

    configs: list[TriggerConfig] = []
    for raw in raw_schedule:
        if not isinstance(raw, dict):
            continue
        try:
            cfg = TriggerConfig.model_validate(raw)
        except Exception:
            continue

        if agent_id is not None:
            payload_agent_id = str(cfg.payload_template.get("agent_id", "")).strip()
            if payload_agent_id and payload_agent_id != agent_id:
                continue
        configs.append(cfg)
    return configs


async def save_trigger_schedule(
    store: Any,
    user_id: str,
    schedule: list[TriggerConfig],
    caller_context: Any | None = None,
) -> None:
    """Persist complete trigger schedule snapshot into UserNode.preferences."""
    node = await _get_user_node(store, user_id, caller_context=caller_context)
    if node is None:
        raise UserNodeNotFoundError(f"User not found: {user_id}")

    preferences = _normalise_preferences(node.get("preferences"))
    preferences[TRIGGER_SCHEDULES_KEY] = [cfg.model_dump(mode="json") for cfg in schedule]

    updated = await _update_user_node(
        store,
        user_id,
        {"preferences": preferences},
        caller_context=caller_context,
    )
    if updated is None:
        raise TriggerPersistenceError(f"Failed to persist trigger schedule for {user_id}")
