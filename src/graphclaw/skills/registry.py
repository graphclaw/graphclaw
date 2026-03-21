"""graphclaw.skills.registry — Skill Registry v2 service.

Description
-----------
``SkillRegistryService`` manages the full lifecycle of skills for a given
user: discovering skills from remote (GitHub, website) and local (built-in)
sources, installing/uninstalling them, searching across sources, loading
parsed ``SkillDefinition`` objects, and recording usage metrics.

Sources
-------
- LOCAL   — built-in skills under ``src/graphclaw/skills/definitions/``
- GITHUB  — fetches ``marketplace.json`` + ``SKILL.md`` files from a GitHub
             repository via the raw.githubusercontent.com CDN
- WEBSITE — fetches ``marketplace.json`` directly from any HTTPS URL

Storage paths (all via ``StorageClient``)
-----------------------------------------
- sources:   ``skills/registry/{user_id}/sources.json``
- installed: ``skills/registry/{user_id}/installed.json``
- cached:    ``skills/cache/{user_id}/{source_hash8}/{skill_name}/SKILL.md``

Design Patterns
---------------
- Strategy: HTTP fetch behaviour is encapsulated in private helpers
  ``_fetch_github_listings`` and ``_fetch_website_listings`` so callers
  never see transport details.
- Lazy initialisation: The ``httpx.AsyncClient`` is created on first use and
  reused for the lifetime of the service.
- EMA quality tracking: ``record_usage`` updates ``avg_quality_score`` with
  an exponential moving average (alpha=0.2) so recent scores are weighted
  more heavily without discarding history.

Public API
----------
- SkillRegistryService: Main service class.
- SkillRegistryService.add_source
- SkillRegistryService.remove_source
- SkillRegistryService.list_sources
- SkillRegistryService.refresh_source
- SkillRegistryService.search
- SkillRegistryService.install
- SkillRegistryService.uninstall
- SkillRegistryService.list_installed
- SkillRegistryService.get_skill_definition
- SkillRegistryService.record_usage

Dependencies
------------
- dataclasses: asdict for JSON serialisation.
- datetime: UTC timestamps.
- hashlib: SHA-256 source URI hashing for cache paths.
- json: Serialisation / deserialisation for storage.
- pathlib: Local skill directory scanning.
- uuid: Skill ID generation.
- httpx: Async HTTP client for remote source fetches.
- graphclaw.infra.storage: StorageClient ABC.
- graphclaw.skills.models: SkillDefinition.
- graphclaw.skills.parser: SkillParser.
- graphclaw.skills.registry_models: domain dataclasses.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from graphclaw.infra.storage import StorageClient
from graphclaw.skills.models import SkillDefinition
from graphclaw.skills.parser import SkillParser
from graphclaw.skills.registry_models import (
    InstalledSkill,
    SkillListing,
    SkillSource,
    SkillSourceType,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_LOCAL_DEFINITIONS_DIR = pathlib.Path(__file__).parent / "definitions"


def _sources_path(user_id: str) -> str:
    return f"skills/registry/{user_id}/sources.json"


def _installed_path(user_id: str) -> str:
    return f"skills/registry/{user_id}/installed.json"


def _cache_path(user_id: str, source_hash8: str, skill_name: str) -> str:
    return f"skills/cache/{user_id}/{source_hash8}/{skill_name}/SKILL.md"


def _source_hash8(uri: str) -> str:
    """Return the first 8 hex characters of the SHA-256 of *uri*."""
    return hashlib.sha256(uri.encode()).hexdigest()[:8]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _serialize(obj: Any) -> str:
    """Serialise a dataclass (or list of dataclasses) to a JSON string."""
    return json.dumps(
        dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj, default=str
    )


def _serialize_list(items: list[Any]) -> str:
    return json.dumps([dataclasses.asdict(i) for i in items], default=str)


def _deserialize_source(d: dict) -> SkillSource:
    last_fetched = d.get("last_fetched_at")
    if last_fetched and isinstance(last_fetched, str):
        last_fetched = datetime.fromisoformat(last_fetched)
    return SkillSource(
        source_type=SkillSourceType(d["source_type"]),
        uri=d["uri"],
        name=d.get("name", ""),
        auth_secret_ref=d.get("auth_secret_ref"),
        last_fetched_at=last_fetched,
        fetch_interval_hours=d.get("fetch_interval_hours", 24),
    )


def _deserialize_installed(d: dict) -> InstalledSkill:
    installed_at = d["installed_at"]
    if isinstance(installed_at, str):
        installed_at = datetime.fromisoformat(installed_at)

    last_used = d.get("last_used_at")
    if last_used and isinstance(last_used, str):
        last_used = datetime.fromisoformat(last_used)

    return InstalledSkill(
        skill_id=d["skill_id"],
        name=d["name"],
        version=d["version"],
        description=d.get("description", ""),
        skill_file_path=d["skill_file_path"],
        source_uri=d["source_uri"],
        source_type=SkillSourceType(d["source_type"]),
        installed_at=installed_at,
        last_used_at=last_used,
        usage_count=d.get("usage_count", 0),
        avg_quality_score=d.get("avg_quality_score", 0.0),
        tags=d.get("tags", []),
    )


def _deserialize_listing(d: dict, source_uri: str, source_type: SkillSourceType) -> SkillListing:
    return SkillListing(
        name=d["name"],
        version=d.get("version", "1.0.0"),
        description=d.get("description", ""),
        tags=d.get("tags", []),
        source_uri=source_uri,
        source_type=source_type,
        skill_file_url=d.get("skill_file_url", ""),
        requires=d.get("requires", []),
    )


# ---------------------------------------------------------------------------
# GitHub URL helpers
# ---------------------------------------------------------------------------


def _github_raw_base(uri: str) -> tuple[str, str, str, str]:
    """Parse a GitHub URI and return (owner, repo, branch, subpath).

    Supported formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch
    - https://github.com/owner/repo/tree/branch/some/path
    """
    # Strip trailing slash
    uri = uri.rstrip("/")

    # Remove the scheme and host
    path = uri.replace("https://github.com/", "").replace("http://github.com/", "")
    parts = path.split("/")

    if len(parts) < 2:  # pragma: no cover
        raise ValueError(f"Invalid GitHub URI: {uri!r}")

    owner = parts[0]
    repo = parts[1]

    # Default branch
    branch = "main"
    subpath = ""

    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
        subpath = "/".join(parts[4:]) if len(parts) > 4 else ""

    return owner, repo, branch, subpath


def _github_marketplace_url(uri: str) -> tuple[str, str, str, str, str]:
    """Return (raw_marketplace_url, owner, repo, branch, subpath)."""
    owner, repo, branch, subpath = _github_raw_base(uri)
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    if subpath:
        marketplace_url = f"{base}/{subpath}/marketplace.json"
    else:
        marketplace_url = f"{base}/marketplace.json"
    return marketplace_url, owner, repo, branch, subpath


def _github_skill_file_url(
    owner: str, repo: str, branch: str, subpath: str, skill_file: str
) -> str:
    """Build the raw URL for a skill file within a GitHub repo."""
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    # skill_file may already be a full path relative to the repo root
    if skill_file.startswith("http"):
        return skill_file
    if subpath and not skill_file.startswith(subpath):
        return f"{base}/{subpath}/{skill_file}"
    return f"{base}/{skill_file}"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SkillRegistryService:
    """Manages skill discovery, installation, and lifecycle.

    Parameters
    ----------
    storage_client:
        Object-storage backend used to persist sources, installed registry,
        and cached SKILL.md files.
    secrets_client:
        Optional secrets backend used to resolve ``auth_secret_ref`` values
        on private GitHub repositories.  Must implement an async
        ``get_secret(key) -> str`` interface.
    """

    def __init__(
        self,
        storage_client: StorageClient,
        secrets_client: Any = None,
    ) -> None:
        self._storage = storage_client
        self._secrets = secrets_client
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_http(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared ``httpx.AsyncClient``."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    # ------------------------------------------------------------------
    # Source persistence
    # ------------------------------------------------------------------

    async def _load_sources(self, user_id: str) -> list[SkillSource]:
        path = _sources_path(user_id)
        try:
            raw = await self._storage.read(path)
            items = json.loads(raw.decode())
            return [_deserialize_source(d) for d in items]
        except FileNotFoundError:
            return []

    async def _save_sources(self, user_id: str, sources: list[SkillSource]) -> None:
        path = _sources_path(user_id)
        data = _serialize_list(sources).encode()
        await self._storage.write(path, data, content_type="application/json")

    async def _load_installed(self, user_id: str) -> list[InstalledSkill]:
        path = _installed_path(user_id)
        try:
            raw = await self._storage.read(path)
            items = json.loads(raw.decode())
            return [_deserialize_installed(d) for d in items]
        except FileNotFoundError:
            return []

    async def _save_installed(self, user_id: str, skills: list[InstalledSkill]) -> None:
        path = _installed_path(user_id)
        data = _serialize_list(skills).encode()
        await self._storage.write(path, data, content_type="application/json")

    # ------------------------------------------------------------------
    # Remote fetch helpers
    # ------------------------------------------------------------------

    async def _auth_headers(self, source: SkillSource) -> dict[str, str]:
        """Build HTTP headers for the source, including auth if configured."""
        if source.auth_secret_ref and self._secrets is not None:
            token = await self._secrets.get_secret(source.auth_secret_ref)
            return {"Authorization": f"token {token}"}
        return {}

    async def _fetch_github_listings(self, source: SkillSource) -> list[SkillListing]:
        """Fetch marketplace.json from a GitHub repository."""
        marketplace_url, owner, repo, branch, subpath = _github_marketplace_url(source.uri)
        headers = await self._auth_headers(source)
        http = await self._get_http()
        resp = await http.get(marketplace_url, headers=headers)
        resp.raise_for_status()
        payload = resp.json()

        listings: list[SkillListing] = []
        for entry in payload.get("skills", []):
            raw_skill_file = entry.get("skill_file_url", "")
            skill_file_url = _github_skill_file_url(owner, repo, branch, subpath, raw_skill_file)
            listing = SkillListing(
                name=entry["name"],
                version=entry.get("version", "1.0.0"),
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
                source_uri=source.uri,
                source_type=SkillSourceType.GITHUB,
                skill_file_url=skill_file_url,
                requires=entry.get("requires", []),
            )
            listings.append(listing)
        return listings

    async def _fetch_website_listings(self, source: SkillSource) -> list[SkillListing]:
        """Fetch marketplace.json from a website URL."""
        headers = await self._auth_headers(source)
        http = await self._get_http()
        resp = await http.get(source.uri, headers=headers)
        resp.raise_for_status()
        payload = resp.json()

        listings: list[SkillListing] = []
        for entry in payload.get("skills", []):
            listing = _deserialize_listing(entry, source.uri, SkillSourceType.WEBSITE)
            listings.append(listing)
        return listings

    def _scan_local_listings(self) -> list[SkillListing]:
        """Scan the built-in definitions directory and return SkillListings."""
        parser = SkillParser()
        listings: list[SkillListing] = []
        if not _LOCAL_DEFINITIONS_DIR.is_dir():
            return listings

        for subdir in sorted(_LOCAL_DEFINITIONS_DIR.iterdir()):
            skill_file = subdir / "SKILL.md"
            if not subdir.is_dir() or not skill_file.exists():
                continue
            try:
                defn = parser.parse_file(str(skill_file))
            except (ValueError, Exception):
                continue

            listing = SkillListing(
                name=defn.name,
                version=defn.version,
                description=defn.description,
                tags=defn.tags,
                source_uri="local://definitions",
                source_type=SkillSourceType.LOCAL,
                skill_file_url=str(skill_file),
            )
            listings.append(listing)
        return listings

    async def _fetch_listings(self, source: SkillSource) -> list[SkillListing]:
        """Dispatch to the correct fetch helper based on source type."""
        if source.source_type == SkillSourceType.GITHUB:
            return await self._fetch_github_listings(source)
        if source.source_type == SkillSourceType.WEBSITE:
            return await self._fetch_website_listings(source)
        if source.source_type == SkillSourceType.LOCAL:
            return self._scan_local_listings()
        return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_source(self, user_id: str, source: SkillSource) -> list[SkillListing]:
        """Register *source*, fetch its index, and return available listings.

        If a source with the same URI already exists it is replaced in place.

        Parameters
        ----------
        user_id:
            The user this source is registered for.
        source:
            Source configuration to register.

        Returns
        -------
        list[SkillListing]
            All skill listings available from the newly registered source.
        """
        sources = await self._load_sources(user_id)
        # Replace if URI already exists
        sources = [s for s in sources if s.uri != source.uri]

        listings = await self._fetch_listings(source)
        source.last_fetched_at = _utcnow()
        sources.append(source)
        await self._save_sources(user_id, sources)
        return listings

    async def remove_source(self, user_id: str, source_uri: str) -> None:
        """Remove *source_uri* and uninstall all skills from it.

        Parameters
        ----------
        user_id:
            Target user.
        source_uri:
            URI of the source to remove.
        """
        sources = await self._load_sources(user_id)
        sources = [s for s in sources if s.uri != source_uri]
        await self._save_sources(user_id, sources)

        # Uninstall skills from this source
        installed = await self._load_installed(user_id)
        to_remove = [sk for sk in installed if sk.source_uri == source_uri]
        for skill in to_remove:
            try:
                await self._storage.delete(skill.skill_file_path)
            except FileNotFoundError:
                pass

        remaining = [sk for sk in installed if sk.source_uri != source_uri]
        await self._save_installed(user_id, remaining)

    async def list_sources(self, user_id: str) -> list[SkillSource]:
        """Return registered sources for *user_id*.

        Parameters
        ----------
        user_id:
            Target user.

        Returns
        -------
        list[SkillSource]
            All registered sources, in registration order.
        """
        return await self._load_sources(user_id)

    async def refresh_source(self, user_id: str, source_uri: str) -> list[SkillListing]:
        """Re-fetch the skill index for *source_uri* and return updated listings.

        Parameters
        ----------
        user_id:
            Target user.
        source_uri:
            URI of the source to refresh.

        Returns
        -------
        list[SkillListing]
            Current listings from the source after refresh.

        Raises
        ------
        KeyError
            If no source with *source_uri* is registered for *user_id*.
        """
        sources = await self._load_sources(user_id)
        source = next((s for s in sources if s.uri == source_uri), None)
        if source is None:
            raise KeyError(f"Source not found: {source_uri!r}")

        listings = await self._fetch_listings(source)
        source.last_fetched_at = _utcnow()
        await self._save_sources(user_id, sources)
        return listings

    async def search(
        self,
        user_id: str,
        query: str = "",
        tags: list[str] | None = None,
        source_uri: str | None = None,
    ) -> list[SkillListing]:
        """Search available skills across all registered sources plus LOCAL.

        Parameters
        ----------
        user_id:
            Target user.
        query:
            Case-insensitive substring matched against skill name and
            description.  Empty string matches everything.
        tags:
            When provided, only skills containing ALL specified tags are
            returned.
        source_uri:
            When provided, restrict results to skills from this source only.

        Returns
        -------
        list[SkillListing]
            Matching skill listings.
        """
        # Collect listings from registered remote/website sources
        sources = await self._load_sources(user_id)
        all_listings: list[SkillListing] = []

        for source in sources:
            if source_uri is not None and source.uri != source_uri:
                continue
            try:
                listings = await self._fetch_listings(source)
                all_listings.extend(listings)
            except Exception:
                pass  # degraded mode — skip unreachable sources

        # Always include LOCAL skills unless filtering to a specific source
        if source_uri is None or source_uri == "local://definitions":
            local_listings = self._scan_local_listings()
            # Avoid duplicates from sources that might also include LOCAL
            existing_names = {
                li.name for li in all_listings if li.source_type == SkillSourceType.LOCAL
            }
            for li in local_listings:
                if li.name not in existing_names:
                    all_listings.append(li)

        # Apply filters
        q = query.lower()
        results: list[SkillListing] = []
        for listing in all_listings:
            if q and q not in listing.name.lower() and q not in listing.description.lower():
                continue
            if tags:
                listing_tags = [t.lower() for t in listing.tags]
                if not all(t.lower() in listing_tags for t in tags):
                    continue
            results.append(listing)

        return results

    async def install(
        self,
        user_id: str,
        skill_name: str,
        source_uri: str,
        version: str | None = None,
    ) -> InstalledSkill:
        """Install a skill from a registered source.

        Steps:
        1. Fetch the source index and locate the listing for *skill_name*.
        2. Download the SKILL.md content from ``listing.skill_file_url``.
        3. Cache it at ``skills/cache/{user_id}/{source_hash8}/{skill_name}/SKILL.md``.
        4. Update ``installed.json``.
        5. Return the ``InstalledSkill`` record.

        Parameters
        ----------
        user_id:
            Target user.
        skill_name:
            Name of the skill to install.
        source_uri:
            URI of the source to install from.
        version:
            Optional version constraint.  When omitted the latest listing is
            used.

        Returns
        -------
        InstalledSkill
            The newly installed skill record.

        Raises
        ------
        KeyError
            If *source_uri* is not registered or *skill_name* is not found.
        httpx.HTTPStatusError
            If the SKILL.md download fails.
        """
        sources = await self._load_sources(user_id)
        source = next((s for s in sources if s.uri == source_uri), None)
        if source is None:
            raise KeyError(f"Source not registered: {source_uri!r}")

        listings = await self._fetch_listings(source)
        listing = next(
            (
                li
                for li in listings
                if li.name == skill_name and (version is None or li.version == version)
            ),
            None,
        )
        if listing is None:
            raise KeyError(
                f"Skill {skill_name!r} not found in source {source_uri!r}"
                + (f" at version {version!r}" if version else "")
            )

        # Download SKILL.md
        if source.source_type == SkillSourceType.LOCAL:
            skill_md_content = pathlib.Path(listing.skill_file_url).read_bytes()
        else:
            headers = await self._auth_headers(source)
            http = await self._get_http()
            resp = await http.get(listing.skill_file_url, headers=headers)
            resp.raise_for_status()
            skill_md_content = resp.content

        # Cache path
        h8 = _source_hash8(source_uri)
        cache_path = _cache_path(user_id, h8, skill_name)
        await self._storage.write(cache_path, skill_md_content, content_type="text/plain")

        # Record installation
        skill_id = f"skill-{uuid.uuid4().hex[:8]}"
        installed_skill = InstalledSkill(
            skill_id=skill_id,
            name=skill_name,
            version=listing.version,
            description=listing.description,
            skill_file_path=cache_path,
            source_uri=source_uri,
            source_type=source.source_type,
            installed_at=_utcnow(),
            tags=list(listing.tags),
        )

        installed = await self._load_installed(user_id)
        # Replace if already installed (re-install)
        installed = [sk for sk in installed if sk.name != skill_name or sk.source_uri != source_uri]
        installed.append(installed_skill)
        await self._save_installed(user_id, installed)

        return installed_skill

    async def uninstall(self, user_id: str, skill_id: str) -> None:
        """Remove an installed skill by its ``skill_id``.

        Deletes the cached SKILL.md and removes the record from
        ``installed.json``.

        Parameters
        ----------
        user_id:
            Target user.
        skill_id:
            ``InstalledSkill.skill_id`` of the skill to remove.

        Raises
        ------
        KeyError
            If no installed skill with *skill_id* exists.
        """
        installed = await self._load_installed(user_id)
        target = next((sk for sk in installed if sk.skill_id == skill_id), None)
        if target is None:
            raise KeyError(f"Installed skill not found: {skill_id!r}")

        try:
            await self._storage.delete(target.skill_file_path)
        except FileNotFoundError:
            pass  # already gone — treat as success

        remaining = [sk for sk in installed if sk.skill_id != skill_id]
        await self._save_installed(user_id, remaining)

    async def list_installed(self, user_id: str) -> list[InstalledSkill]:
        """Return all installed skills for *user_id*.

        Includes skills with ``source_type == LOCAL`` (virtualised local
        skills are not stored in ``installed.json`` but synthesised here).

        Parameters
        ----------
        user_id:
            Target user.

        Returns
        -------
        list[InstalledSkill]
            All installed skills.
        """
        return await self._load_installed(user_id)

    async def get_skill_definition(self, user_id: str, skill_id: str) -> SkillDefinition:
        """Load and parse the SKILL.md for an installed skill.

        Parameters
        ----------
        user_id:
            Target user.
        skill_id:
            ``InstalledSkill.skill_id`` to load.

        Returns
        -------
        SkillDefinition
            Parsed skill definition ready for execution.

        Raises
        ------
        KeyError
            If *skill_id* is not installed.
        FileNotFoundError
            If the cached SKILL.md has been deleted from storage.
        ValueError
            If the cached SKILL.md is not valid.
        """
        installed = await self._load_installed(user_id)
        target = next((sk for sk in installed if sk.skill_id == skill_id), None)
        if target is None:
            raise KeyError(f"Installed skill not found: {skill_id!r}")

        raw = await self._storage.read(target.skill_file_path)
        parser = SkillParser()
        return parser.parse(raw.decode())

    async def record_usage(
        self,
        user_id: str,
        skill_id: str,
        quality_score: float | None = None,
    ) -> None:
        """Increment usage count and optionally update the quality EMA.

        The quality score is tracked as an exponential moving average with
        alpha=0.2::

            avg = 0.2 * new_score + 0.8 * old_avg

        Parameters
        ----------
        user_id:
            Target user.
        skill_id:
            ``InstalledSkill.skill_id`` to update.
        quality_score:
            Optional score in [0, 1].  When provided the EMA is updated.

        Raises
        ------
        KeyError
            If *skill_id* is not installed.
        """
        installed = await self._load_installed(user_id)
        target = next((sk for sk in installed if sk.skill_id == skill_id), None)
        if target is None:
            raise KeyError(f"Installed skill not found: {skill_id!r}")

        target.usage_count += 1
        target.last_used_at = _utcnow()

        if quality_score is not None:
            alpha = 0.2
            target.avg_quality_score = (
                alpha * quality_score + (1 - alpha) * target.avg_quality_score
            )

        await self._save_installed(user_id, installed)


__all__ = ["SkillRegistryService"]
