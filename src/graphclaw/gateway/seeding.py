# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.seeding — Idempotent system content seeder.

Seeds the following objects into MinIO on gateway startup if they don't already exist:

  system/prompts/system_header.md     ← main agent system prompt header
  system/knowledge/*.md               ← 6 domain knowledge files
  system/agents/comms/profile.md      ← comms agent persona
  system/agents/comms/manifest.json   ← comms agent manifest
  system/agents/comms/config.json     ← comms agent channel config
    system/skills/definitions/*/SKILL.md ← built-in runtime skill definitions

All writes are idempotent — existing objects are never overwritten.

Prompt content is stored in gateway/prompts/ alongside this module so it can be
edited as plain Markdown/JSON without touching Python source.

Public API
----------
- seed_system_content(storage): Seed all system content on startup.
"""

from __future__ import annotations

import logging
from pathlib import Path

from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SKILLS_DEFINITIONS_DIR = Path(__file__).resolve().parents[1] / "skills" / "definitions"

_KNOWLEDGE_TOPICS = [
    "node_creation_rules",
    "edge_creation_rules",
    "state_machine_rules",
    "goal_inference_rules",
    "goal_lifecycle_rules",
    "scoring_context",
    "follow_up_timing",
    "constraint_rules",
    "resource_rules",
]

_SYSTEM_AGENTS = ["comms", "inbound"]


def _iter_system_skill_definition_files() -> list[tuple[str, Path]]:
    """Return sorted (skill_name, skill_md_path) tuples for built-in skills."""
    if not _SKILLS_DEFINITIONS_DIR.exists():
        return []

    discovered: list[tuple[str, Path]] = []
    for skill_dir in sorted(_SKILLS_DEFINITIONS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and skill_file.is_file():
            discovered.append((skill_dir.name, skill_file))
    return discovered


# ---------------------------------------------------------------------------
# Main seeding function
# ---------------------------------------------------------------------------


async def seed_system_content(storage: StorageClient) -> None:
    """Seed all system content into object storage. Idempotent — skips existing objects.

    Parameters
    ----------
    storage:
        The configured StorageClient (MinIO / S3).
    """
    seeded = 0
    skipped = 0

    async def _seed(path: str, content: bytes, content_type: str = "text/plain") -> None:
        nonlocal seeded, skipped
        try:
            exists = await storage.exists(path)
            if exists:
                skipped += 1
                return
            await storage.write(path, content, content_type=content_type)
            seeded += 1
            logger.info("seeding: wrote %s", path)
        except Exception as exc:
            logger.warning("seeding: failed to write %s — %s", path, exc)

    # 1. System prompt header
    await _seed(
        StoragePaths.system_prompt_header(),
        (_PROMPTS_DIR / "system_header.md").read_bytes(),
        "text/markdown",
    )

    # 2. Knowledge files
    for topic in _KNOWLEDGE_TOPICS:
        await _seed(
            StoragePaths.system_knowledge(topic),
            (_PROMPTS_DIR / "knowledge" / f"{topic}.md").read_bytes(),
            "text/markdown",
        )

    # 3. System agents
    for agent_id in _SYSTEM_AGENTS:
        agent_dir = _PROMPTS_DIR / "agents" / agent_id
        await _seed(
            StoragePaths.system_agent_profile(agent_id),
            (agent_dir / "profile.md").read_bytes(),
            "text/markdown",
        )
        await _seed(
            StoragePaths.system_agent_manifest(agent_id),
            (agent_dir / "manifest.json").read_bytes(),
            "application/json",
        )
        await _seed(
            StoragePaths.system_agent_config(agent_id),
            (agent_dir / "config.json").read_bytes(),
            "application/json",
        )

    # 4. System skill definitions
    skill_files = _iter_system_skill_definition_files()
    if not skill_files:
        logger.warning(
            "seeding: no system skill definitions found at %s",
            _SKILLS_DEFINITIONS_DIR,
        )

    for skill_name, skill_file in skill_files:
        await _seed(
            StoragePaths.system_skill_definition(skill_name),
            skill_file.read_bytes(),
            "text/markdown",
        )

    logger.info(
        "seeding: complete — seeded=%d skipped=%d",
        seeded,
        skipped,
    )


__all__ = ["seed_system_content"]
