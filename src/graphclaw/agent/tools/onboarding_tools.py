"""graphclaw.agent.tools.onboarding_tools — Onboarding tool definitions (FR-ID-001).

Description
-----------
Defines the per-state tool functions used during the onboarding FSM:
  ``set_user_name``, ``set_user_persona``, ``add_user_identity``,
  ``set_working_hours``, ``set_preferences``, ``seed_policy_from_template``,
  ``complete_onboarding``.

Each tool updates the appropriate node or policy file and is called by the
comms agent while in an active onboarding state.

Public API
----------
- ONBOARDING_TOOLS: list of tool dicts (compatible with tool_registry format).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def set_user_name(
    user_id: str,
    name: str,
    store: Any,
    **_: Any,
) -> dict:
    """Update the user's display name on their UserNode."""
    await store.update_node(user_id, {"name": name})
    return {"updated": True, "name": name}


async def set_user_persona(
    user_id: str,
    role: str,
    timezone: str | None = None,
    store: Any = None,
    **_: Any,
) -> dict:
    """Update role and optionally timezone on the user's UserNode."""
    updates: dict[str, Any] = {"role": role}
    if timezone:
        updates["timezone"] = timezone
    if store:
        await store.update_node(user_id, updates)
    return {"updated": True, "role": role, "timezone": timezone}


async def add_user_identity(
    user_id: str,
    channel: str,
    value: str,
    store: Any = None,
    **_: Any,
) -> dict:
    """Add a channel identity entry to the user's UserNode.identities.

    ``channel`` is one of: ``email``, ``phone``, ``telegram_id``,
    ``telegram_username``, ``whatsapp_id``, ``slack_user_id``.
    """
    if store is None:
        return {"updated": False, "error": "store not provided"}
    try:
        node_raw = await store.get_node(user_id)
        if node_raw is None:
            return {"updated": False, "error": "user not found"}
        identities = node_raw.get("identities", {}) if isinstance(node_raw, dict) else {}
        if not isinstance(identities, dict):
            identities = {}

        # Multi-value channels use lists; single-value use scalar
        list_channels = {"emails", "phones"}
        key = (
            channel
            if channel in {"telegram_id", "telegram_username", "whatsapp_id", "slack_user_id"}
            else channel
        )
        if channel in list_channels or channel in {"emails", "phones"}:
            existing = identities.get(key, [])
            if not isinstance(existing, list):
                existing = [existing] if existing else []
            if value not in existing:
                existing.append(value)
            identities[key] = existing
        else:
            identities[key] = value

        await store.update_node(user_id, {"identities": identities})
        return {"updated": True, "channel": channel, "value": value}
    except Exception as exc:  # noqa: BLE001
        logger.warning("add_user_identity failed: %s", exc)
        return {"updated": False, "error": str(exc)}


async def set_working_hours(
    user_id: str,
    start: str,
    end: str,
    store: Any = None,
    **_: Any,
) -> dict:
    """Update working hours on the user's UserNode."""
    if store is None:
        return {"updated": False}
    await store.update_node(user_id, {"working_hours": {"start": start, "end": end}})
    return {"updated": True, "start": start, "end": end}


async def set_preferences(
    user_id: str,
    preferred_channel: str | None = None,
    briefing_time: str | None = None,
    briefing_style: str | None = None,
    default_follow_up_days: int | None = None,
    store: Any = None,
    **_: Any,
) -> dict:
    """Update UserPreferences on the user's UserNode."""
    if store is None:
        return {"updated": False}
    try:
        node_raw = await store.get_node(user_id)
        prefs = node_raw.get("preferences", {}) if isinstance(node_raw, dict) else {}
        if not isinstance(prefs, dict):
            prefs = {}
        if preferred_channel:
            prefs["preferred_channel"] = preferred_channel
        if briefing_time:
            prefs["briefing_time"] = briefing_time
        if briefing_style:
            prefs["briefing_style"] = briefing_style
        if default_follow_up_days is not None:
            prefs["default_follow_up_days"] = default_follow_up_days
        await store.update_node(user_id, {"preferences": prefs})
        return {"updated": True, "preferences": prefs}
    except Exception as exc:  # noqa: BLE001
        return {"updated": False, "error": str(exc)}


async def seed_policy_from_template(
    user_id: str,
    agent_id: str = "main",
    policy_name: str = "delegation",
    storage: Any = None,
    **_: Any,
) -> dict:
    """Seed a default policy file from the built-in template."""
    if storage is None:
        return {"seeded": False, "error": "storage not provided"}
    try:
        from graphclaw.agent.policies.loader import PolicyLoader  # noqa: PLC0415
        from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

        loader = PolicyLoader(storage)
        path = StoragePaths.agent_policy(user_id, agent_id, policy_name)
        exists = await storage.exists(path)
        if exists:
            return {"seeded": False, "reason": "already_exists", "path": path}

        # Load default template
        template_content = _get_default_template(policy_name)
        await storage.write(path, template_content.encode("utf-8"), "text/markdown")
        return {"seeded": True, "policy_name": policy_name, "path": path}
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_policy_from_template failed: %s", exc)
        return {"seeded": False, "error": str(exc)}


async def complete_onboarding(
    user_id: str,
    agent_id: str = "main",
    storage: Any = None,
    **_: Any,
) -> dict:
    """Mark onboarding as complete in profile.md (FR-ID-001 AC3)."""
    if storage is None:
        return {"completed": False, "error": "storage not provided"}
    try:
        from graphclaw.agent.onboarding import OnboardingFSM  # noqa: PLC0415

        fsm = OnboardingFSM(storage)
        await fsm.complete(user_id, agent_id)
        return {"completed": True}
    except Exception as exc:  # noqa: BLE001
        return {"completed": False, "error": str(exc)}


def _get_default_template(policy_name: str) -> str:
    """Return the default template body for *policy_name*."""
    templates = {
        "delegation": (
            "---\n"
            "fail_mode: closed\n"
            "auto_acknowledge: true\n"
            "accept_deadline_extension_max_days: 3\n"
            "allowed_state_transitions:\n"
            "  - {from: WAITING, to: IN_PROGRESS}\n"
            "escalate_on_blocker: true\n"
            "recipient_overrides: {}\n"
            "---\n\n"
            "# Delegation Policy\n\n"
            "Allow the agent to acknowledge tasks and move them to IN_PROGRESS. "
            "Deadline extensions up to 3 days are permitted. "
            "Escalate when a task becomes blocked.\n"
        ),
        "escalation": (
            "---\n"
            "fail_mode: closed\n"
            "interrupt_on_score_above: 0.8\n"
            "quiet_hours_start: '22:00'\n"
            "quiet_hours_end: '07:00'\n"
            "on_owner_unreachable_after_hours: 24\n"
            "---\n\n"
            "# Escalation Policy\n\n"
            "Interrupt the owner for high-priority items (score > 0.8). "
            "Respect quiet hours. Wait 24h before taking conservative fallback action.\n"
        ),
        "counterparty_etiquette": (
            "---\n"
            "fail_mode: degraded\n"
            "tone: professional\n"
            "max_message_length: 500\n"
            "allow_attachments: false\n"
            "---\n\n"
            "# Counterparty Etiquette Policy\n\n"
            "Maintain a professional, concise tone. Keep messages under 500 words. "
            "Do not share internal task details.\n"
        ),
        "reply_tone": (
            "---\n"
            "fail_mode: degraded\n"
            "voice: neutral\n"
            "sign_off: 'Best,'\n"
            "---\n\n"
            "# Reply Tone Policy\n\n"
            "Use a neutral, friendly voice. Sign off messages professionally.\n"
        ),
    }
    return templates.get(
        policy_name,
        f"---\nfail_mode: degraded\n---\n\n# {policy_name.replace('_', ' ').title()} Policy\n\nNo default content.\n",
    )


# ---------------------------------------------------------------------------
# Tool registry entries
# ---------------------------------------------------------------------------

ONBOARDING_TOOLS: list[dict] = [
    {
        "name": "set_user_name",
        "description": "Set or update the user's display name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The user's full name."},
            },
            "required": ["name"],
        },
        "fn": set_user_name,
    },
    {
        "name": "set_user_persona",
        "description": "Set the user's role and timezone.",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "timezone": {"type": "string"},
            },
            "required": ["role"],
        },
        "fn": set_user_persona,
    },
    {
        "name": "add_user_identity",
        "description": "Add a channel identity (email, phone, telegram, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["channel", "value"],
        },
        "fn": add_user_identity,
    },
    {
        "name": "set_working_hours",
        "description": "Set working hours (HH:MM format).",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["start", "end"],
        },
        "fn": set_working_hours,
    },
    {
        "name": "set_preferences",
        "description": "Set user preferences (channel, briefing style, follow-up days).",
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_channel": {"type": "string"},
                "briefing_time": {"type": "string"},
                "briefing_style": {"type": "string"},
                "default_follow_up_days": {"type": "integer"},
            },
            "required": [],
        },
        "fn": set_preferences,
    },
    {
        "name": "seed_policy_from_template",
        "description": "Seed a default policy file (delegation, escalation, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "policy_name": {
                    "type": "string",
                    "enum": ["delegation", "escalation", "counterparty_etiquette", "reply_tone"],
                },
            },
            "required": ["policy_name"],
        },
        "fn": seed_policy_from_template,
    },
    {
        "name": "complete_onboarding",
        "description": "Mark onboarding as complete and unlock the full agent.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": complete_onboarding,
    },
]
