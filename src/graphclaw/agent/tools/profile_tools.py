# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.tools.profile_tools — Post-onboarding behavioral profile updates.

Description
-----------
Defines ``update_profile_from_conversation``, the tool the main orchestrator calls
when the user expresses a change in HOW they want the agent to behave (tone,
verbosity, proactivity, interruption threshold).  It appends a single bullet to the
``## Working Style`` or ``## Key Preferences`` section of the user's ``profile.md``.

Behavioral guidance ONLY lives here.  Structured facts (timezone, working hours,
channels, briefing time/style) belong on the ``UserNode`` and are handled by the
onboarding tools — keeping the two stores from duplicating each other.

Public API
----------
- update_profile_from_conversation: Append a behavioral instruction to profile.md.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_SECTION_HEADERS = {
    "working_style": "## Working Style",
    "preferences": "## Key Preferences",
}


def _append_to_section(body: str, header: str, instruction: str) -> str:
    """Append ``- instruction`` to *header*'s section, creating it if absent."""
    bullet = f"- {instruction}"
    if header in body:
        # Insert the bullet at the end of the section's existing bullet list,
        # i.e. just before the next "## " heading or end of document.
        pattern = rf"({re.escape(header)}[ \t]*\n(?:.*?\n)*?)(?=\n## |\Z)"

        def _insert(match: re.Match[str]) -> str:
            section = match.group(1).rstrip("\n")
            return f"{section}\n{bullet}\n"

        return re.sub(pattern, _insert, body, count=1, flags=re.DOTALL)
    # Section missing — create it at the top of the body.
    prefix = f"{header}\n\n{bullet}\n"
    return f"{prefix}\n{body.strip()}".strip() + "\n" if body.strip() else prefix


async def update_profile_from_conversation(
    user_id: str,
    instruction: str,
    section: str = "preferences",
    agent_id: str = "main",
    storage: Any = None,
    **_: Any,
) -> dict:
    """Append a behavioral *instruction* to the user's profile.md.

    Parameters
    ----------
    user_id, agent_id:
        Owner and agent identifiers.
    instruction:
        The behavioral preference to remember (one short sentence).
    section:
        ``"working_style"`` or ``"preferences"`` (default ``"preferences"``).
    storage:
        Write-scoped ``StorageClient`` for profile.md.

    Returns
    -------
    dict
        ``{"updated": bool, ...}``.  On success includes the added instruction
        and section; on failure includes an ``error`` message.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        return {"updated": False, "error": "No instruction provided"}
    if storage is None:
        return {"updated": False, "error": "storage not provided"}

    header = _SECTION_HEADERS.get(section)
    if header is None:
        section = "preferences"
        header = _SECTION_HEADERS[section]

    try:
        from graphclaw.agent.onboarding import _render_profile, _split_frontmatter  # noqa: PLC0415
        from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

        path = StoragePaths.agent_profile(user_id, agent_id)
        try:
            raw = await storage.read(path)
            content = raw.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return {
                "updated": False,
                "error": "Profile not found — complete onboarding first",
            }

        frontmatter, body = _split_frontmatter(content)
        body = _append_to_section(body, header, instruction)
        new_content = _render_profile(frontmatter, body)
        await storage.write(path, new_content.encode("utf-8"), "text/markdown")

        logger.info(
            "profile.updated_from_conversation",
            extra={"user_id": user_id, "agent_id": agent_id, "section": section},
        )
        return {"updated": True, "section": section, "instruction_added": instruction}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_profile_from_conversation failed: %s", exc)
        return {"updated": False, "error": str(exc)}


__all__ = ["update_profile_from_conversation"]
