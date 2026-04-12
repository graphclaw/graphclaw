"""tests.test_skills.test_registry — Unit tests for SkillRegistryService.

Description
-----------
Covers skill source registration, remote index fetching (GitHub and website),
search/filter, install/uninstall, definition loading, and usage recording.

Design Patterns
---------------
- Arrange/Act/Assert: All tests follow the AAA structure.
- Dependency Injection: StorageClient is an AsyncMock so no real I/O occurs.
- HTTP mocking: httpx is patched via ``unittest.mock.patch`` to intercept
  network calls for GitHub and website sources.

Dependencies
------------
- pytest, pytest-asyncio: Test runner and async support.
- unittest.mock: AsyncMock, MagicMock, patch for isolating dependencies.
- graphclaw.skills.registry: SkillRegistryService under test.
- graphclaw.skills.registry_models: Domain dataclasses.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.skills.registry import (
    SkillRegistryService,
    _github_marketplace_url,
    _source_hash8,
)
from graphclaw.skills.registry_models import (
    InstalledSkill,
    SkillSource,
    SkillSourceType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)

_SAMPLE_SKILL_MD = (
    "---\n"
    "name: test-skill\n"
    "description: A test skill\n"
    "version: 1.2.0\n"
    "tags:\n"
    "  - test\n"
    "  - demo\n"
    "---\n"
    "You are a test assistant.\n"
)

_MARKETPLACE_PAYLOAD = {
    "name": "Test Marketplace",
    "description": "Skills for testing",
    "publisher": "test-org",
    "version": "1.0",
    "skills": [
        {
            "name": "test-skill",
            "version": "1.2.0",
            "description": "A test skill for demos",
            "tags": ["test", "demo"],
            "skill_file_url": "skills/test-skill/SKILL.md",
            "requires": [],
        },
        {
            "name": "another-skill",
            "version": "2.0.0",
            "description": "Another useful skill",
            "tags": ["utility"],
            "skill_file_url": "skills/another-skill/SKILL.md",
            "requires": [],
        },
    ],
}


def _make_storage() -> AsyncMock:
    """Return an AsyncMock that simulates an empty StorageClient."""
    storage = AsyncMock()
    # Default: read raises FileNotFoundError (empty storage)
    storage.read.side_effect = FileNotFoundError("not found")
    storage.write.return_value = None
    storage.delete.return_value = None
    storage.exists.return_value = False
    return storage


def _make_http_response(payload: dict | str | bytes, status_code: int = 200) -> MagicMock:
    """Return a MagicMock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(payload, (dict, list)):
        resp.json.return_value = payload
        resp.content = json.dumps(payload).encode()
    elif isinstance(payload, str):
        resp.json.side_effect = ValueError("not json")
        resp.content = payload.encode()
    else:
        resp.json.side_effect = ValueError("not json")
        resp.content = payload
    resp.raise_for_status = MagicMock()
    return resp


def _make_service(storage: AsyncMock | None = None) -> SkillRegistryService:
    s = storage or _make_storage()
    return SkillRegistryService(storage_client=s)


def _installed_json(skills: list[InstalledSkill]) -> bytes:
    return json.dumps([dataclasses.asdict(sk) for sk in skills], default=str).encode()


# ---------------------------------------------------------------------------
# Test 1: add_source LOCAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_source_local() -> None:
    """LOCAL source should return built-in SkillListings from definitions dir."""
    storage = _make_storage()
    # Empty sources at start
    storage.read.side_effect = FileNotFoundError("empty")
    service = _make_service(storage)

    source = SkillSource(source_type=SkillSourceType.LOCAL, uri="local://definitions")
    listings = await service.add_source("user-1", source)

    # Should have discovered at least the built-in skills in definitions/
    assert isinstance(listings, list)
    # All listings must carry the correct source metadata
    for li in listings:
        assert li.source_type == SkillSourceType.LOCAL
        assert li.source_uri == "local://definitions"
        assert li.name  # non-empty name
    # Storage should have been written (sources.json persisted)
    storage.write.assert_called()


# ---------------------------------------------------------------------------
# Test 2: add_source GITHUB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_source_github() -> None:
    """GITHUB source: mocked httpx returns marketplace.json, listings returned."""
    storage = _make_storage()
    storage.read.side_effect = FileNotFoundError("empty")
    service = _make_service(storage)

    source = SkillSource(
        source_type=SkillSourceType.GITHUB,
        uri="https://github.com/test-org/skills-repo",
    )

    mock_resp = _make_http_response(_MARKETPLACE_PAYLOAD)
    with patch.object(service, "_get_http", new=AsyncMock()) as mock_get_http:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_http.return_value = mock_client

        listings = await service.add_source("user-1", source)

    assert len(listings) == 2  # noqa: PLR2004
    names = {li.name for li in listings}
    assert "test-skill" in names
    assert "another-skill" in names
    for li in listings:
        assert li.source_type == SkillSourceType.GITHUB
        assert li.source_uri == source.uri
        # skill_file_url should have been converted to raw.githubusercontent.com
        assert "raw.githubusercontent.com" in li.skill_file_url


# ---------------------------------------------------------------------------
# Test 3: search by query (case-insensitive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_query() -> None:
    """search() should match query case-insensitively against name and description."""
    storage = _make_storage()
    storage.read.side_effect = FileNotFoundError("empty")
    service = _make_service(storage)

    source = SkillSource(
        source_type=SkillSourceType.GITHUB,
        uri="https://github.com/test-org/skills-repo",
    )

    mock_resp = _make_http_response(_MARKETPLACE_PAYLOAD)
    with patch.object(service, "_get_http", new=AsyncMock()) as mock_get_http:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_http.return_value = mock_client
        await service.add_source("user-1", source)

        # Reload with sources registered
        sources_json = json.dumps([dataclasses.asdict(source)], default=str).encode()

        def _side_effect(path: str) -> bytes:
            if "sources.json" in path:
                return sources_json
            raise FileNotFoundError(path)

        storage.read.side_effect = _side_effect
        mock_client.get = AsyncMock(return_value=mock_resp)

        results = await service.search("user-1", query="TEST")

    # "test-skill" matches on name; "A test skill for demos" matches on description
    result_names = {r.name for r in results}
    assert "test-skill" in result_names


# ---------------------------------------------------------------------------
# Test 4: search by tags (ALL tags must match)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_tags() -> None:
    """search() with tags= must return only skills that have ALL specified tags."""
    storage = _make_storage()
    service = _make_service(storage)

    source = SkillSource(
        source_type=SkillSourceType.GITHUB,
        uri="https://github.com/test-org/skills-repo",
    )

    mock_resp = _make_http_response(_MARKETPLACE_PAYLOAD)
    with patch.object(service, "_get_http", new=AsyncMock()) as mock_get_http:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_http.return_value = mock_client
        await service.add_source("user-1", source)

        sources_json = json.dumps([dataclasses.asdict(source)], default=str).encode()

        def _side_effect(path: str) -> bytes:
            if "sources.json" in path:
                return sources_json
            raise FileNotFoundError(path)

        storage.read.side_effect = _side_effect
        mock_client.get = AsyncMock(return_value=mock_resp)

        # Both "test" and "demo" appear only on test-skill
        results = await service.search("user-1", tags=["test", "demo"])

    result_names = {r.name for r in results}
    assert "test-skill" in result_names
    assert "another-skill" not in result_names  # only has "utility"


# ---------------------------------------------------------------------------
# Test 5: search by source_uri
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_source_uri() -> None:
    """search() with source_uri= should restrict results to that source only."""
    storage = _make_storage()
    service = _make_service(storage)

    github_source = SkillSource(
        source_type=SkillSourceType.GITHUB,
        uri="https://github.com/test-org/skills-repo",
    )

    mock_resp = _make_http_response(_MARKETPLACE_PAYLOAD)
    with patch.object(service, "_get_http", new=AsyncMock()) as mock_get_http:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_http.return_value = mock_client
        await service.add_source("user-1", github_source)

        sources_json = json.dumps([dataclasses.asdict(github_source)], default=str).encode()

        def _side_effect(path: str) -> bytes:
            if "sources.json" in path:
                return sources_json
            raise FileNotFoundError(path)

        storage.read.side_effect = _side_effect
        mock_client.get = AsyncMock(return_value=mock_resp)

        # Filter to GitHub source only — LOCAL skills excluded
        results = await service.search(
            "user-1", source_uri="https://github.com/test-org/skills-repo"
        )

    for r in results:
        assert r.source_uri == "https://github.com/test-org/skills-repo"


# ---------------------------------------------------------------------------
# Test 6: install from GitHub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_from_github() -> None:
    """install() should download SKILL.md and write it to the correct cache path."""
    storage = _make_storage()
    service = _make_service(storage)

    source = SkillSource(
        source_type=SkillSourceType.GITHUB,
        uri="https://github.com/test-org/skills-repo",
    )

    sources_json = json.dumps([dataclasses.asdict(source)], default=str).encode()

    def _storage_read(path: str) -> bytes:
        if "sources.json" in path:
            return sources_json
        raise FileNotFoundError(path)

    storage.read.side_effect = _storage_read

    marketplace_resp = _make_http_response(_MARKETPLACE_PAYLOAD)
    skill_md_resp = _make_http_response(_SAMPLE_SKILL_MD.encode())
    skill_md_resp.json.side_effect = ValueError("not json")
    skill_md_resp.content = _SAMPLE_SKILL_MD.encode()

    call_count = 0

    async def _mock_get(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if "marketplace.json" in url:
            return marketplace_resp
        return skill_md_resp

    with patch.object(service, "_get_http", new=AsyncMock()) as mock_get_http:
        mock_client = AsyncMock()
        mock_client.get = _mock_get
        mock_get_http.return_value = mock_client

        installed = await service.install("user-1", "test-skill", source.uri)

    assert installed.name == "test-skill"
    assert installed.version == "1.2.0"
    assert installed.source_type == SkillSourceType.GITHUB

    # Verify cache path structure — user_id is now the root prefix
    h8 = _source_hash8(source.uri)
    expected_path = f"user-1/skills/cache/{h8}/test-skill/SKILL.md"
    assert installed.skill_file_path == expected_path

    # StorageClient.write should have been called with SKILL.md content and the path
    write_calls = storage.write.call_args_list
    cache_write = next(
        (c for c in write_calls if "SKILL.md" in c.args[0]),
        None,
    )
    assert cache_write is not None, "Expected a write call for SKILL.md"
    assert cache_write.args[0] == expected_path
    assert (
        _SAMPLE_SKILL_MD.encode() in cache_write.args[1]
        or cache_write.args[1] == _SAMPLE_SKILL_MD.encode()
    )


# ---------------------------------------------------------------------------
# Test 7: uninstall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall() -> None:
    """uninstall() should call storage.delete and remove from installed list."""
    storage = _make_storage()
    service = _make_service(storage)

    skill = InstalledSkill(
        skill_id="skill-abc12345",
        name="test-skill",
        version="1.0.0",
        description="Test",
        skill_file_path="skills/cache/user-1/abcd1234/test-skill/SKILL.md",
        source_uri="https://github.com/org/repo",
        source_type=SkillSourceType.GITHUB,
        installed_at=_NOW,
        tags=["test"],
    )

    installed_json_bytes = _installed_json([skill])

    def _storage_read(path: str) -> bytes:
        if "installed.json" in path:
            return installed_json_bytes
        raise FileNotFoundError(path)

    storage.read.side_effect = _storage_read

    await service.uninstall("user-1", "skill-abc12345")

    # delete should have been called with the cached SKILL.md path
    storage.delete.assert_called_once_with(skill.skill_file_path)

    # installed.json should have been written back with an empty list
    write_calls = storage.write.call_args_list
    installed_write = next(
        (c for c in write_calls if "installed.json" in c.args[0]),
        None,
    )
    assert installed_write is not None
    saved = json.loads(installed_write.args[1].decode())
    assert saved == []


# ---------------------------------------------------------------------------
# Test 8: list_installed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_installed() -> None:
    """list_installed() should read installed.json and return InstalledSkill list."""
    storage = _make_storage()
    service = _make_service(storage)

    skill_a = InstalledSkill(
        skill_id="skill-aaaa0001",
        name="skill-a",
        version="1.0.0",
        description="Skill A",
        skill_file_path="skills/cache/user-1/hash1/skill-a/SKILL.md",
        source_uri="https://github.com/org/repo",
        source_type=SkillSourceType.GITHUB,
        installed_at=_NOW,
    )
    skill_b = InstalledSkill(
        skill_id="skill-bbbb0002",
        name="skill-b",
        version="2.0.0",
        description="Skill B",
        skill_file_path="skills/cache/user-1/hash1/skill-b/SKILL.md",
        source_uri="https://example.com/marketplace.json",
        source_type=SkillSourceType.WEBSITE,
        installed_at=_NOW,
    )

    installed_json_bytes = _installed_json([skill_a, skill_b])

    def _storage_read(path: str) -> bytes:
        if "installed.json" in path:
            return installed_json_bytes
        raise FileNotFoundError(path)

    storage.read.side_effect = _storage_read

    result = await service.list_installed("user-1")

    assert len(result) == 2  # noqa: PLR2004
    names = {r.name for r in result}
    assert "skill-a" in names
    assert "skill-b" in names


# ---------------------------------------------------------------------------
# Test 9: get_skill_definition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_skill_definition() -> None:
    """get_skill_definition() should read cached SKILL.md and parse via SkillParser."""
    storage = _make_storage()
    service = _make_service(storage)

    skill = InstalledSkill(
        skill_id="skill-def00001",
        name="test-skill",
        version="1.2.0",
        description="Test",
        skill_file_path="skills/cache/user-1/abcd1234/test-skill/SKILL.md",
        source_uri="https://github.com/org/repo",
        source_type=SkillSourceType.GITHUB,
        installed_at=_NOW,
    )
    installed_json_bytes = _installed_json([skill])

    def _storage_read(path: str) -> bytes:
        if "installed.json" in path:
            return installed_json_bytes
        if "SKILL.md" in path:
            return _SAMPLE_SKILL_MD.encode()
        raise FileNotFoundError(path)

    storage.read.side_effect = _storage_read

    defn = await service.get_skill_definition("user-1", "skill-def00001")

    assert defn.name == "test-skill"
    assert defn.version == "1.2.0"
    assert defn.description == "A test skill"
    assert defn.system_prompt == "You are a test assistant."
    assert "test" in defn.tags
    assert "demo" in defn.tags


# ---------------------------------------------------------------------------
# Test 10: record_usage increments count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_count() -> None:
    """record_usage() should increment usage_count and set last_used_at."""
    storage = _make_storage()
    service = _make_service(storage)

    skill = InstalledSkill(
        skill_id="skill-use00001",
        name="test-skill",
        version="1.0.0",
        description="Test",
        skill_file_path="skills/cache/user-1/h/test-skill/SKILL.md",
        source_uri="https://github.com/org/repo",
        source_type=SkillSourceType.GITHUB,
        installed_at=_NOW,
        usage_count=5,
        last_used_at=None,
    )
    installed_json_bytes = _installed_json([skill])

    call_count = 0

    def _storage_read(path: str) -> bytes:
        nonlocal call_count
        if "installed.json" in path:
            call_count += 1
            return installed_json_bytes
        raise FileNotFoundError(path)

    storage.read.side_effect = _storage_read

    # Capture what gets written back
    written_data: list[bytes] = []

    async def _write(path: str, data: bytes, content_type: str = "text/plain") -> None:
        if "installed.json" in path:
            written_data.append(data)
            nonlocal installed_json_bytes
            installed_json_bytes = data

    storage.write.side_effect = _write

    await service.record_usage("user-1", "skill-use00001")

    assert written_data, "installed.json should have been written"
    saved = json.loads(written_data[-1].decode())
    assert saved[0]["usage_count"] == 6  # noqa: PLR2004
    assert saved[0]["last_used_at"] is not None


# ---------------------------------------------------------------------------
# Test 11: record_usage EMA quality score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_ema() -> None:
    """record_usage() EMA: avg = 0.2 * new_score + 0.8 * old_avg."""
    storage = _make_storage()
    service = _make_service(storage)

    old_avg = 0.5
    new_score = 1.0
    expected_avg = 0.2 * new_score + 0.8 * old_avg  # = 0.6

    skill = InstalledSkill(
        skill_id="skill-ema00001",
        name="test-skill",
        version="1.0.0",
        description="Test",
        skill_file_path="skills/cache/user-1/h/test-skill/SKILL.md",
        source_uri="https://github.com/org/repo",
        source_type=SkillSourceType.GITHUB,
        installed_at=_NOW,
        avg_quality_score=old_avg,
    )

    installed_json_bytes = _installed_json([skill])
    written_data: list[bytes] = []

    def _storage_read(path: str) -> bytes:
        if "installed.json" in path:
            return installed_json_bytes
        raise FileNotFoundError(path)

    async def _write(path: str, data: bytes, content_type: str = "text/plain") -> None:
        if "installed.json" in path:
            written_data.append(data)

    storage.read.side_effect = _storage_read
    storage.write.side_effect = _write

    await service.record_usage("user-1", "skill-ema00001", quality_score=new_score)

    assert written_data, "installed.json should have been written"
    saved = json.loads(written_data[-1].decode())
    assert abs(saved[0]["avg_quality_score"] - expected_avg) < 1e-9


# ---------------------------------------------------------------------------
# Test 12: GitHub raw URL conversion
# ---------------------------------------------------------------------------


def test_github_raw_url_conversion() -> None:
    """_github_marketplace_url() should convert github.com URLs to raw CDN URLs."""
    # Basic repo URL
    url, owner, repo, branch, subpath = _github_marketplace_url(
        "https://github.com/my-org/my-skills"
    )
    assert url == "https://raw.githubusercontent.com/my-org/my-skills/main/marketplace.json"
    assert owner == "my-org"
    assert repo == "my-skills"
    assert branch == "main"
    assert subpath == ""

    # URL with explicit branch and subpath
    url2, owner2, repo2, branch2, subpath2 = _github_marketplace_url(
        "https://github.com/my-org/my-skills/tree/develop/skill-packs"
    )
    assert url2 == (
        "https://raw.githubusercontent.com/my-org/my-skills/develop/skill-packs/marketplace.json"
    )
    assert owner2 == "my-org"
    assert repo2 == "my-skills"
    assert branch2 == "develop"
    assert subpath2 == "skill-packs"

    # URL with trailing slash
    url3, *_ = _github_marketplace_url("https://github.com/org/repo/")
    assert url3 == "https://raw.githubusercontent.com/org/repo/main/marketplace.json"
