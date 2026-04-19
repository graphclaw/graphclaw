"""graphclaw.infra.storage — StorageClient ABC, S3StorageClient, and StoragePaths.

Description
-----------
Provides the abstract ``StorageClient`` interface that all object-storage
backends must implement, plus a concrete ``S3StorageClient`` that wraps
boto3 and supports both AWS S3 and MinIO (via ``endpoint_url`` override).
All blocking boto3 calls are wrapped in ``asyncio.to_thread`` so the event
loop is never blocked.

Also provides ``StoragePaths`` — the single source of truth for all storage
path conventions across the multi-tenant system.  Every module that reads or
writes to object storage MUST use ``StoragePaths`` instead of constructing
path strings inline.

Design Patterns
---------------
- Abstract Base Class: ``StorageClient`` defines the minimal contract so
  production (S3) and test (in-memory stub) backends are interchangeable.
- Adapter: ``S3StorageClient`` adapts the synchronous boto3 API to async.
- Template Method: ``_get_client`` is an internal helper that creates the
  boto3 client on demand, keeping the constructor lightweight.
- Static path registry: ``StoragePaths`` uses only static/class methods so it
  can be imported and called without instantiation, keeping call sites terse.

Public API
----------
- StorageClient: ABC with read, write, delete, list_objects, exists.
- S3StorageClient: boto3/MinIO-backed implementation.
- StoragePaths: Static path factory for all multi-tenant object-storage paths.

Dependencies
------------
- abc: ABC, abstractmethod.
- asyncio: to_thread for sync-to-async bridge.
- boto3: AWS SDK (also used against MinIO).
- botocore.exceptions: ClientError for 404 detection in exists().

Multi-tenant storage layout
---------------------------
The bucket root is partitioned by ``user_id`` so that RBAC policies can grant
each authenticated user access ONLY to the ``{user_id}/`` prefix.  No user
data ever lives outside their own prefix.

  {bucket}/
  ├── system/
  │   └── skills/definitions/{skill_name}/SKILL.md     ← seeded built-ins (read-only for users)
  │
  └── {user_id}/
      ├── config.json                                   ← user app settings
      ├── scoring_weights.json                          ← 7-factor scoring weights
      ├── agents/{agent_id}/
      │   ├── profile.md                                ← agent persona / goals / style
      │   ├── config.json                               ← agent operational config
      │   └── memory/
      │       ├── episodic/{date}-{session_id}.md       ← time-ordered session summaries
      │       ├── semantic/{topic}.md                   ← long-term factual knowledge
      │       └── working/context.md                    ← current session scratchpad
      ├── skills/
      │   ├── registry/sources.json                     ← registered remote sources
      │   ├── registry/installed.json                   ← installed skill metadata
      │   ├── cache/{source_hash}/{skill_name}/SKILL.md ← downloaded remote skills
      │   ├── authored/{skill_id}/SKILL.md              ← user-authored / forked skills
      │   └── executions/{skill_id}.json                ← execution history
      └── attachments/{channel}/{date}/{msg_id}/{file}  ← inbound message attachments
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# StoragePaths — single source of truth for all multi-tenant path conventions
# ---------------------------------------------------------------------------


class StoragePaths:
    """Static factory for all multi-tenant object-storage paths.

    Every module that reads from or writes to object storage MUST use these
    helpers instead of constructing path strings inline.  This ensures the
    entire codebase stays consistent when paths need to change and makes
    per-user data isolation easy to audit.

    Path root
    ---------
    All user-specific data lives under ``{user_id}/``.  System-level data
    (seeded built-in skills) lives under ``system/``.  No cross-tenant
    access is possible when RBAC is applied at the ``{user_id}/`` prefix.
    """

    # ------------------------------------------------------------------
    # System paths (shared, read-only for regular users)
    # ------------------------------------------------------------------

    @staticmethod
    def system_skill_definition(skill_name: str) -> str:
        """Path for a seeded built-in skill definition.

        Example: ``system/skills/definitions/meeting-notes-agent/SKILL.md``
        """
        return f"system/skills/definitions/{skill_name}/SKILL.md"

    @staticmethod
    def system_skills_prefix() -> str:
        """Prefix to list all system skill definitions."""
        return "system/skills/definitions/"

    # ------------------------------------------------------------------
    # User root
    # ------------------------------------------------------------------

    @staticmethod
    def user_root(user_id: str) -> str:
        """Root prefix for all objects owned by *user_id*."""
        return f"{user_id}/"

    # ------------------------------------------------------------------
    # User-level config
    # ------------------------------------------------------------------

    @staticmethod
    def user_config(user_id: str) -> str:
        """User application settings JSON.

        Example: ``usr-abc123/config.json``
        """
        return f"{user_id}/config.json"

    @staticmethod
    def user_scoring_weights(user_id: str) -> str:
        """User scoring weight overrides JSON.

        Example: ``usr-abc123/scoring_weights.json``
        """
        return f"{user_id}/scoring_weights.json"

    # ------------------------------------------------------------------
    # Agent paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_root(user_id: str, agent_id: str) -> str:
        """Root prefix for a single agent's objects."""
        return f"{user_id}/agents/{agent_id}/"

    @staticmethod
    def agent_profile(user_id: str, agent_id: str) -> str:
        """Agent persona / goals / style document.

        Example: ``usr-abc123/agents/main/profile.md``
        """
        return f"{user_id}/agents/{agent_id}/profile.md"

    @staticmethod
    def agent_config(user_id: str, agent_id: str) -> str:
        """Agent operational config JSON (heartbeat, LLM selection, tools).

        Example: ``usr-abc123/agents/main/config.json``
        """
        return f"{user_id}/agents/{agent_id}/config.json"

    # ------------------------------------------------------------------
    # Agent memory paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_memory_root(user_id: str, agent_id: str) -> str:
        """Root prefix for all memory objects of one agent."""
        return f"{user_id}/agents/{agent_id}/memory/"

    @staticmethod
    def agent_memory_working(user_id: str, agent_id: str) -> str:
        """Current-session working context stream.

        Inbound intelligence notes are appended as timestamped entries.

        Example: ``usr-abc123/agents/main/memory/working/context.md``
        """
        return f"{user_id}/agents/{agent_id}/memory/working/context.md"

    @staticmethod
    def agent_intelligence_archive(user_id: str, agent_id: str, task_id: str, date: str) -> str:
        """Archive path for trimmed task intelligence spillover.

        Example:
        ``usr-abc123/agents/main/intelligence/archive/TSK-XYZ/2026-04-19.md``
        """
        return f"{user_id}/agents/{agent_id}/intelligence/archive/{task_id}/{date}.md"

    @staticmethod
    def agent_memory_episodic_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all episodic memory entries for an agent."""
        return f"{user_id}/agents/{agent_id}/memory/episodic/"

    @staticmethod
    def agent_memory_episodic_entry(user_id: str, agent_id: str, entry_name: str) -> str:
        """One episodic memory entry (time-ordered session summary).

        Example: ``usr-abc123/agents/main/memory/episodic/2026-04-11-ses-abc.md``
        """
        return f"{user_id}/agents/{agent_id}/memory/episodic/{entry_name}"

    @staticmethod
    def agent_memory_semantic_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all semantic memory topics for an agent."""
        return f"{user_id}/agents/{agent_id}/memory/semantic/"

    @staticmethod
    def agent_memory_semantic_topic(user_id: str, agent_id: str, topic: str) -> str:
        """One semantic memory topic (long-term factual knowledge).

        Example: ``usr-abc123/agents/main/memory/semantic/users.md``
        """
        return f"{user_id}/agents/{agent_id}/memory/semantic/{topic}.md"

    # ------------------------------------------------------------------
    # Skill registry paths
    # ------------------------------------------------------------------

    @staticmethod
    def skill_registry_sources(user_id: str) -> str:
        """Registered remote skill sources JSON.

        Example: ``usr-abc123/skills/registry/sources.json``
        """
        return f"{user_id}/skills/registry/sources.json"

    @staticmethod
    def skill_registry_installed(user_id: str) -> str:
        """Installed skill metadata JSON.

        Example: ``usr-abc123/skills/registry/installed.json``
        """
        return f"{user_id}/skills/registry/installed.json"

    @staticmethod
    def skill_cache(user_id: str, source_hash8: str, skill_name: str) -> str:
        """Cached SKILL.md downloaded from a remote source.

        Example: ``usr-abc123/skills/cache/a1b2c3d4/meeting-notes-agent/SKILL.md``
        """
        return f"{user_id}/skills/cache/{source_hash8}/{skill_name}/SKILL.md"

    @staticmethod
    def skill_authored(user_id: str, skill_id: str) -> str:
        """User-authored or forked skill definition.

        Example: ``usr-abc123/skills/authored/my-skill-id/SKILL.md``
        """
        return f"{user_id}/skills/authored/{skill_id}/SKILL.md"

    @staticmethod
    def skill_authored_prefix(user_id: str) -> str:
        """Prefix to list all user-authored skills."""
        return f"{user_id}/skills/authored/"

    @staticmethod
    def skill_executions(user_id: str, skill_id: str) -> str:
        """Execution history JSON for one installed skill.

        Example: ``usr-abc123/skills/executions/skill-abc123.json``
        """
        return f"{user_id}/skills/executions/{skill_id}.json"

    # ------------------------------------------------------------------
    # MCP server config paths
    # ------------------------------------------------------------------

    @staticmethod
    def mcp_servers_prefix(user_id: str) -> str:
        """Prefix to list all registered MCP server configs for a user.

        Example: ``USER-abc123/mcp/servers/``
        """
        return f"{user_id}/mcp/servers/"

    @staticmethod
    def mcp_server(user_id: str, server_id: str) -> str:
        """One MCP server config JSON, isolated under the user's prefix.

        Example: ``USER-abc123/mcp/servers/MCP-github-dev-001.json``
        """
        return f"{user_id}/mcp/servers/{server_id}.json"

    # ------------------------------------------------------------------
    # Attachment paths
    # ------------------------------------------------------------------

    @staticmethod
    def attachment(user_id: str, channel: str, date_str: str, msg_id: str, filename: str) -> str:
        """Inbound message attachment stored per-user, per-channel.

        Example: ``usr-abc123/attachments/whatsapp/2026-04-11/msg-xyz/abc_file.jpg``
        """
        safe_msg_id = msg_id.replace("/", "_")
        return f"{user_id}/attachments/{channel}/{date_str}/{safe_msg_id}/{filename}"

    @staticmethod
    def attachments_prefix(user_id: str) -> str:
        """Prefix to list all attachments for a user."""
        return f"{user_id}/attachments/"

    # ------------------------------------------------------------------
    # System prompt paths
    # ------------------------------------------------------------------

    @staticmethod
    def system_prompt_header() -> str:
        """Main agent system prompt header (editable without redeployment).

        Example: ``system/prompts/system_header.md``
        """
        return "system/prompts/system_header.md"

    @staticmethod
    def system_prompts_prefix() -> str:
        """Prefix to list all system prompt files."""
        return "system/prompts/"

    # ------------------------------------------------------------------
    # System knowledge paths
    # ------------------------------------------------------------------

    @staticmethod
    def system_knowledge(topic: str) -> str:
        """One domain knowledge document for the agent.

        Example: ``system/knowledge/node_creation_rules.md``
        """
        return f"system/knowledge/{topic}.md"

    @staticmethod
    def system_knowledge_prefix() -> str:
        """Prefix to list all system knowledge documents."""
        return "system/knowledge/"

    # ------------------------------------------------------------------
    # System agent paths
    # ------------------------------------------------------------------

    @staticmethod
    def system_agent_root(agent_id: str) -> str:
        """Root prefix for a system-level agent's objects.

        Example: ``system/agents/comms/``
        """
        return f"system/agents/{agent_id}/"

    @staticmethod
    def system_agent_profile(agent_id: str) -> str:
        """System agent persona / instructions document.

        Example: ``system/agents/comms/profile.md``
        """
        return f"system/agents/{agent_id}/profile.md"

    @staticmethod
    def system_agent_manifest(agent_id: str) -> str:
        """System agent manifest JSON (capabilities, tool_hint, invocation type).

        Example: ``system/agents/comms/manifest.json``
        """
        return f"system/agents/{agent_id}/manifest.json"

    @staticmethod
    def system_agent_config(agent_id: str) -> str:
        """System agent operational config JSON.

        Example: ``system/agents/comms/config.json``
        """
        return f"system/agents/{agent_id}/config.json"

    @staticmethod
    def system_agents_prefix() -> str:
        """Prefix to list all system agent directories."""
        return "system/agents/"

    # ------------------------------------------------------------------
    # User agent manifest paths (complement to existing agent_profile/config)
    # ------------------------------------------------------------------

    @staticmethod
    def agent_manifest(user_id: str, agent_id: str) -> str:
        """User-created agent manifest JSON (capabilities, tool_hint).

        Example: ``usr-abc123/agents/main/manifest.json``
        """
        return f"{user_id}/agents/{agent_id}/manifest.json"

    @staticmethod
    def agents_prefix(user_id: str) -> str:
        """Prefix to list all agent directories for a user.

        Example: ``usr-abc123/agents/``
        """
        return f"{user_id}/agents/"

    # ------------------------------------------------------------------
    # Agent inbox paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_inbox_recent_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all recent inbox entries for an agent.

        Example: ``usr-abc123/agents/main/inbox/recent/``
        """
        return f"{user_id}/agents/{agent_id}/inbox/recent/"

    @staticmethod
    def agent_inbox_recent(user_id: str, agent_id: str, entry_name: str) -> str:
        """One recent inbox entry for an agent.

        Example: ``usr-abc123/agents/main/inbox/recent/2026-04-12-msg-xyz.md``
        """
        return f"{user_id}/agents/{agent_id}/inbox/recent/{entry_name}"

    @staticmethod
    def agent_inbox_archive(user_id: str, agent_id: str, entry_name: str) -> str:
        """One archived inbox entry for an agent.

        Example: ``usr-abc123/agents/main/inbox/archive/2026-04-11-msg-abc.md``
        """
        return f"{user_id}/agents/{agent_id}/inbox/archive/{entry_name}"


class StorageClient(ABC):
    """Abstract interface for object storage backends."""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read the object at *path* and return its raw bytes.

        Args:
            path: Object key / path within the bucket.

        Returns:
            Raw bytes of the stored object.

        Raises:
            FileNotFoundError: If the object does not exist.
        """

    @abstractmethod
    async def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "text/plain",
    ) -> None:
        """Write *data* to *path*.

        Args:
            path: Object key / path within the bucket.
            data: Raw bytes to store.
            content_type: MIME type stored as object metadata.
        """

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete the object at *path*.

        Args:
            path: Object key / path within the bucket.
        """

    @abstractmethod
    async def list_objects(self, prefix: str) -> list[str]:
        """List all object keys that start with *prefix*.

        Args:
            prefix: Key prefix to filter by (e.g. ``"agents/USER-abc/"``).

        Returns:
            Sorted list of matching object keys.
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Return True if *path* exists in the bucket.

        Args:
            path: Object key / path within the bucket.
        """


class S3StorageClient(StorageClient):
    """boto3-backed StorageClient supporting AWS S3 and MinIO.

    Args:
        bucket: Name of the S3 bucket to operate on.
        endpoint_url: Override for MinIO or other S3-compatible servers.
            Leave as None to use the default AWS endpoint.
        region: AWS region name (default ``"us-east-1"``).
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._client: object | None = None  # lazy init

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> object:
        """Return (or lazily create) the boto3 S3 client."""
        if self._client is None:
            import boto3  # local import to keep module importable without boto3

            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url is not None:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._aws_access_key_id is not None:
                kwargs["aws_access_key_id"] = self._aws_access_key_id
            if self._aws_secret_access_key is not None:
                kwargs["aws_secret_access_key"] = self._aws_secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    # ------------------------------------------------------------------
    # StorageClient interface
    # ------------------------------------------------------------------

    async def read(self, path: str) -> bytes:
        """Read object at *path* from S3/MinIO.

        Raises
        ------
        FileNotFoundError
            If the key does not exist (S3 NoSuchKey / 404).
        """

        def _read() -> bytes:
            import botocore.exceptions  # local import keeps module importable without botocore

            client = self._get_client()
            try:
                response = client.get_object(Bucket=self._bucket, Key=path)
                return response["Body"].read()
            except botocore.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise FileNotFoundError(
                        f"Object not found: s3://{self._bucket}/{path}"
                    ) from exc
                raise

        return await asyncio.to_thread(_read)

    async def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "text/plain",
    ) -> None:
        """Write *data* to *path* in S3/MinIO."""

        def _write() -> None:
            client = self._get_client()
            client.put_object(
                Bucket=self._bucket,
                Key=path,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_write)

    async def delete(self, path: str) -> None:
        """Delete object at *path* from S3/MinIO."""

        def _delete() -> None:
            client = self._get_client()
            client.delete_object(Bucket=self._bucket, Key=path)

        await asyncio.to_thread(_delete)

    async def list_objects(self, prefix: str) -> list[str]:
        """List all keys with *prefix* in S3/MinIO."""

        def _list() -> list[str]:
            client = self._get_client()
            paginator = client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return sorted(keys)

        return await asyncio.to_thread(_list)

    async def exists(self, path: str) -> bool:
        """Return True if *path* exists in S3/MinIO."""

        def _exists() -> bool:
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                client.head_object(Bucket=self._bucket, Key=path)
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    return False
                raise

        return await asyncio.to_thread(_exists)
