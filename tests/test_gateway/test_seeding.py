"""Unit tests for file-driven gateway seeding behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphclaw.gateway.seeding import seed_system_content
from graphclaw.infra.storage import StoragePaths


class _InMemoryStorage:
    """Minimal async storage stub for seeding tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def exists(self, path: str) -> bool:
        return path in self.objects

    async def write(
        self, path: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self.objects[path] = (data, content_type)


def _local_skill_names() -> list[str]:
    skills_root = (
        Path(__file__).resolve().parents[2] / "src" / "graphclaw" / "skills" / "definitions"
    )
    if not skills_root.exists():
        return []

    names: list[str] = []
    for skill_dir in sorted(skills_root.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            names.append(skill_dir.name)
    return names


@pytest.mark.asyncio
async def test_seed_system_content_writes_builtin_skill_definitions() -> None:
    storage = _InMemoryStorage()

    await seed_system_content(storage)

    skill_names = _local_skill_names()
    assert skill_names, "No local built-in skills found to validate seeding"

    for skill_name in skill_names:
        path = StoragePaths.system_skill_definition(skill_name)
        assert path in storage.objects
        payload, content_type = storage.objects[path]
        assert b"---" in payload
        assert content_type == "text/markdown"


@pytest.mark.asyncio
async def test_seed_system_content_is_idempotent_for_skill_definition() -> None:
    storage = _InMemoryStorage()
    skill_names = _local_skill_names()
    assert skill_names, "No local built-in skills found to validate idempotency"

    sample_skill = skill_names[0]
    sample_path = StoragePaths.system_skill_definition(sample_skill)
    original = b"# custom skill\n"
    storage.objects[sample_path] = (original, "text/markdown")

    await seed_system_content(storage)

    stored, _content_type = storage.objects[sample_path]
    assert stored == original
