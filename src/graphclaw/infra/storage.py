# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
    │       └── working/archive/{entry}.md            ← durable snapshots after compaction
      ├── skills/
      │   ├── registry/sources.json                     ← registered remote sources
      │   ├── registry/installed.json                   ← installed skill metadata
      │   ├── cache/{source_hash}/{skill_name}/SKILL.md ← downloaded remote skills
      │   ├── authored/{skill_id}/SKILL.md              ← user-authored / forked skills
      │   └── executions/{skill_id}.json                ← execution history
    ├── sessions/{session_id}/                         ← per-session artifacts
    │   ├── context.md
    │   ├── events/{event_id}.json
    │   └── outputs/{artifact_name}
      └── attachments/{channel}/{date}/{msg_id}/{file}  ← inbound message attachments
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _validate_segment(value: str, field_name: str) -> str:
        """Validate a single path segment to prevent traversal/injection."""
        segment = value.strip()
        if not segment:
            raise ValueError(f"{field_name} cannot be empty")
        if "/" in segment or "\\" in segment:
            raise ValueError(f"{field_name} must not contain path separators")
        if segment in {".", ".."} or ".." in segment:
            raise ValueError(f"{field_name} must not contain traversal tokens")
        if "\x00" in segment:
            raise ValueError(f"{field_name} must not contain null bytes")
        return segment

    @classmethod
    def _validate_relative_path(cls, value: str, field_name: str) -> str:
        """Validate a slash-delimited relative path with safe segments."""
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty")
        if normalized.startswith("/") or normalized.endswith("/"):
            raise ValueError(f"{field_name} must be a relative path")
        segments = [cls._validate_segment(part, field_name) for part in normalized.split("/")]
        return "/".join(segments)

    # ------------------------------------------------------------------
    # System paths (shared, read-only for regular users)
    # ------------------------------------------------------------------

    @staticmethod
    def system_skill_definition(skill_name: str) -> str:
        """Path for a seeded built-in skill definition.

        Example: ``system/skills/definitions/meeting-notes-agent/SKILL.md``
        """
        skill = StoragePaths._validate_segment(skill_name, "skill_name")
        return f"system/skills/definitions/{skill}/SKILL.md"

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
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/"

    # ------------------------------------------------------------------
    # User-level config
    # ------------------------------------------------------------------

    @staticmethod
    def user_config(user_id: str) -> str:
        """User application settings JSON.

        Example: ``usr-abc123/config.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/config.json"

    @staticmethod
    def user_scoring_weights(user_id: str) -> str:
        """User scoring weight overrides JSON.

        Example: ``usr-abc123/scoring_weights.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/scoring_weights.json"

    @staticmethod
    def chat_history(user_id: str) -> str:
        """User-scoped chat history JSON path.

        Example: ``usr-abc123/chat/history.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/chat/history.json"

    # ------------------------------------------------------------------
    # Agent paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_root(user_id: str, agent_id: str) -> str:
        """Root prefix for a single agent's objects."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/"

    @staticmethod
    def agent_profile(user_id: str, agent_id: str) -> str:
        """Agent persona / goals / style document.

        Example: ``usr-abc123/agents/main/profile.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/profile.md"

    @staticmethod
    def agent_config(user_id: str, agent_id: str) -> str:
        """Agent operational config JSON (heartbeat, LLM selection, tools).

        Example: ``usr-abc123/agents/main/config.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/config.json"

    # ------------------------------------------------------------------
    # Agent memory paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_memory_root(user_id: str, agent_id: str) -> str:
        """Root prefix for all memory objects of one agent."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/"

    @staticmethod
    def agent_memory_working(user_id: str, agent_id: str) -> str:
        """Current-session working context stream.

        Inbound intelligence notes are appended as timestamped entries.

        Example: ``usr-abc123/agents/main/memory/working/context.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/working/context.md"

    @staticmethod
    def agent_memory_working_archive_prefix(user_id: str, agent_id: str) -> str:
        """Prefix for archived working-context snapshots.

        Example: ``usr-abc123/agents/main/memory/working/archive/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/working/archive/"

    @staticmethod
    def agent_memory_working_archive_entry(user_id: str, agent_id: str, entry_name: str) -> str:
        """One archived working-context snapshot.

        Example: ``usr-abc123/agents/main/memory/working/archive/2026-04-20-compact-ses1.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        entry = StoragePaths._validate_segment(entry_name, "entry_name")
        return f"{user}/agents/{agent}/memory/working/archive/{entry}"

    @staticmethod
    def agent_intelligence_archive(user_id: str, agent_id: str, task_id: str, date: str) -> str:
        """Archive path for trimmed task intelligence spillover.

        Example:
        ``usr-abc123/agents/main/intelligence/archive/TSK-XYZ/2026-04-19.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        task = StoragePaths._validate_segment(task_id, "task_id")
        date_segment = StoragePaths._validate_segment(date, "date")
        return f"{user}/agents/{agent}/intelligence/archive/{task}/{date_segment}.md"

    @staticmethod
    def agent_memory_episodic_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all episodic memory entries for an agent."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/episodic/"

    @staticmethod
    def agent_memory_episodic_entry(user_id: str, agent_id: str, entry_name: str) -> str:
        """One episodic memory entry (time-ordered session summary).

        Example: ``usr-abc123/agents/main/memory/episodic/2026-04-11-ses-abc.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        entry = StoragePaths._validate_segment(entry_name, "entry_name")
        return f"{user}/agents/{agent}/memory/episodic/{entry}"

    @staticmethod
    def agent_memory_episodic_archive_prefix(user_id: str, agent_id: str) -> str:
        """Prefix for archived episodic entries (never loaded into agent context).

        Example: ``usr-abc123/agents/main/memory/episodic/archive/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/episodic/archive/"

    @staticmethod
    def agent_memory_episodic_archive_entry(user_id: str, agent_id: str, entry_name: str) -> str:
        """One archived episodic entry (permanently excluded from agent context).

        Example: ``usr-abc123/agents/main/memory/episodic/archive/2026-04-20-compact-sprint12.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        entry = StoragePaths._validate_segment(entry_name, "entry_name")
        return f"{user}/agents/{agent}/memory/episodic/archive/{entry}"

    @staticmethod
    def agent_memory_semantic_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all semantic memory topics for an agent."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/semantic/"

    @staticmethod
    def agent_memory_semantic_topic(user_id: str, agent_id: str, topic: str) -> str:
        """One semantic memory topic (long-term factual knowledge).

        Example: ``usr-abc123/agents/main/memory/semantic/users.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        topic_segment = StoragePaths._validate_segment(topic, "topic")
        return f"{user}/agents/{agent}/memory/semantic/{topic_segment}.md"

    @staticmethod
    def agent_memory_semantic_index(user_id: str, agent_id: str) -> str:
        """Index file describing all semantic memory topics for an agent.

        Example: ``usr-abc123/agents/main/memory/semantic/_index.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/memory/semantic/_index.json"

    # ------------------------------------------------------------------
    # Skill registry paths
    # ------------------------------------------------------------------

    @staticmethod
    def skill_registry_sources(user_id: str) -> str:
        """Registered remote skill sources JSON.

        Example: ``usr-abc123/skills/registry/sources.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/skills/registry/sources.json"

    @staticmethod
    def skill_registry_installed(user_id: str) -> str:
        """Installed skill metadata JSON.

        Example: ``usr-abc123/skills/registry/installed.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/skills/registry/installed.json"

    @staticmethod
    def skill_cache(user_id: str, source_hash8: str, skill_name: str) -> str:
        """Cached SKILL.md downloaded from a remote source.

        Example: ``usr-abc123/skills/cache/a1b2c3d4/meeting-notes-agent/SKILL.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        source_hash = StoragePaths._validate_segment(source_hash8, "source_hash8")
        skill = StoragePaths._validate_segment(skill_name, "skill_name")
        return f"{user}/skills/cache/{source_hash}/{skill}/SKILL.md"

    @staticmethod
    def skill_authored(user_id: str, skill_id: str) -> str:
        """User-authored or forked skill definition.

        Example: ``usr-abc123/skills/authored/my-skill-id/SKILL.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        skill = StoragePaths._validate_segment(skill_id, "skill_id")
        return f"{user}/skills/authored/{skill}/SKILL.md"

    @staticmethod
    def skill_authored_prefix(user_id: str) -> str:
        """Prefix to list all user-authored skills."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/skills/authored/"

    @staticmethod
    def skill_executions(user_id: str, skill_id: str) -> str:
        """Execution history JSON for one installed skill.

        Example: ``usr-abc123/skills/executions/skill-abc123.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        skill = StoragePaths._validate_segment(skill_id, "skill_id")
        return f"{user}/skills/executions/{skill}.json"

    # ------------------------------------------------------------------
    # MCP server config paths
    # ------------------------------------------------------------------

    @staticmethod
    def mcp_servers_prefix(user_id: str) -> str:
        """Prefix to list all registered MCP server configs for a user.

        Example: ``USER-abc123/mcp/servers/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/mcp/servers/"

    @staticmethod
    def mcp_server(user_id: str, server_id: str) -> str:
        """One MCP server config JSON, isolated under the user's prefix.

        Example: ``USER-abc123/mcp/servers/MCP-github-dev-001.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        server = StoragePaths._validate_segment(server_id, "server_id")
        return f"{user}/mcp/servers/{server}.json"

    # ------------------------------------------------------------------
    # Attachment paths
    # ------------------------------------------------------------------

    @staticmethod
    def attachment(user_id: str, channel: str, date_str: str, msg_id: str, filename: str) -> str:
        """Inbound message attachment stored per-user, per-channel.

        Example: ``usr-abc123/attachments/whatsapp/2026-04-11/msg-xyz/abc_file.jpg``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        channel_segment = StoragePaths._validate_segment(channel, "channel")
        date_segment = StoragePaths._validate_segment(date_str, "date_str")
        safe_msg_id = StoragePaths._validate_segment(msg_id.replace("/", "_"), "msg_id")
        file_segment = StoragePaths._validate_segment(filename, "filename")
        return f"{user}/attachments/{channel_segment}/{date_segment}/{safe_msg_id}/{file_segment}"

    @staticmethod
    def attachments_prefix(user_id: str) -> str:
        """Prefix to list all attachments for a user."""
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/attachments/"

    # ------------------------------------------------------------------
    # Session artifact paths
    # ------------------------------------------------------------------

    @staticmethod
    def session_root(user_id: str, session_id: str) -> str:
        """Root prefix for a single user session's artifacts.

        Example: ``usr-abc123/sessions/SES-1234/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        return f"{user}/sessions/{session}/"

    @staticmethod
    def session_context(user_id: str, session_id: str) -> str:
        """Canonical per-session context snapshot path.

        Example: ``usr-abc123/sessions/SES-1234/context.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        return f"{user}/sessions/{session}/context.md"

    @staticmethod
    def session_events_prefix(user_id: str, session_id: str) -> str:
        """Prefix containing structured session events.

        Example: ``usr-abc123/sessions/SES-1234/events/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        return f"{user}/sessions/{session}/events/"

    @staticmethod
    def session_event(user_id: str, session_id: str, event_id: str) -> str:
        """One structured session event payload.

        Example: ``usr-abc123/sessions/SES-1234/events/evt-0001.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        event = StoragePaths._validate_segment(event_id, "event_id")
        return f"{user}/sessions/{session}/events/{event}.json"

    @staticmethod
    def session_outputs_prefix(user_id: str, session_id: str) -> str:
        """Prefix containing generated per-session artifacts.

        Example: ``usr-abc123/sessions/SES-1234/outputs/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        return f"{user}/sessions/{session}/outputs/"

    @staticmethod
    def session_output(user_id: str, session_id: str, artifact_name: str) -> str:
        """One generated per-session artifact.

        Example: ``usr-abc123/sessions/SES-1234/outputs/briefing.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        session = StoragePaths._validate_segment(session_id, "session_id")
        artifact = StoragePaths._validate_segment(artifact_name, "artifact_name")
        return f"{user}/sessions/{session}/outputs/{artifact}"

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
        topic_segment = StoragePaths._validate_segment(topic, "topic")
        return f"system/knowledge/{topic_segment}.md"

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
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"system/agents/{agent}/"

    @staticmethod
    def system_agent_profile(agent_id: str) -> str:
        """System agent persona / instructions document.

        Example: ``system/agents/comms/profile.md``
        """
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"system/agents/{agent}/profile.md"

    @staticmethod
    def system_agent_manifest(agent_id: str) -> str:
        """System agent manifest JSON (capabilities, tool_hint, invocation type).

        Example: ``system/agents/comms/manifest.json``
        """
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"system/agents/{agent}/manifest.json"

    @staticmethod
    def system_agent_config(agent_id: str) -> str:
        """System agent operational config JSON.

        Example: ``system/agents/comms/config.json``
        """
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"system/agents/{agent}/config.json"

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
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/manifest.json"

    @staticmethod
    def agents_prefix(user_id: str) -> str:
        """Prefix to list all agent directories for a user.

        Example: ``usr-abc123/agents/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/agents/"

    # ------------------------------------------------------------------
    # Agent inbox paths
    # ------------------------------------------------------------------

    @staticmethod
    def agent_inbox_recent_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all recent inbox entries for an agent.

        Example: ``usr-abc123/agents/main/inbox/recent/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/inbox/recent/"

    @staticmethod
    def agent_inbox_recent(user_id: str, agent_id: str, entry_name: str) -> str:
        """One recent inbox entry for an agent.

        Example: ``usr-abc123/agents/main/inbox/recent/2026-04-12-msg-xyz.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        entry = StoragePaths._validate_segment(entry_name, "entry_name")
        return f"{user}/agents/{agent}/inbox/recent/{entry}"

    @staticmethod
    def agent_inbox_archive(user_id: str, agent_id: str, entry_name: str) -> str:
        """One archived inbox entry for an agent.

        Example: ``usr-abc123/agents/main/inbox/archive/2026-04-11-msg-abc.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        entry = StoragePaths._validate_segment(entry_name, "entry_name")
        return f"{user}/agents/{agent}/inbox/archive/{entry}"

    # ------------------------------------------------------------------
    # Log paths
    # ------------------------------------------------------------------

    @staticmethod
    def user_log_path(user_id: str, service: str, hour_key: str, extension: str = "jsonl") -> str:
        """User-scoped log object path.

        Example: ``usr-abc123/logs/gateway/2026-04-19/1000Z.jsonl``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        service_segment = StoragePaths._validate_segment(service, "service")
        hour_path = StoragePaths._validate_relative_path(hour_key, "hour_key")
        ext = StoragePaths._validate_segment(extension, "extension")
        return f"{user}/logs/{service_segment}/{hour_path}.{ext}"

    @staticmethod
    def system_log_path(service: str, hour_key: str, extension: str = "jsonl") -> str:
        """System-scoped log object path.

        Example: ``system/logs/gateway/2026-04-19/1000Z.jsonl``
        """
        service_segment = StoragePaths._validate_segment(service, "service")
        hour_path = StoragePaths._validate_relative_path(hour_key, "hour_key")
        ext = StoragePaths._validate_segment(extension, "extension")
        return f"system/logs/{service_segment}/{hour_path}.{ext}"

    # ------------------------------------------------------------------
    # Wave 1 — Conversation storage (FR-STORE-001)
    # ------------------------------------------------------------------

    @staticmethod
    def conversation_thread(
        user_id: str, counterparty_id: str, channel: str, thread_id: str
    ) -> str:
        """JSONL log for one thread between owner and counterparty.

        Example:
        ``USER-abc/conversations/RES-bob/telegram/tg-thread-001.jsonl``

        For owner self-chat (cockpit), counterparty_id == user_id.
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        counterparty = StoragePaths._validate_segment(counterparty_id, "counterparty_id")
        chan = StoragePaths._validate_segment(channel, "channel")
        thread = StoragePaths._validate_segment(thread_id, "thread_id")
        return f"{user}/conversations/{counterparty}/{chan}/{thread}.jsonl"

    @staticmethod
    def conversation_index(user_id: str) -> str:
        """Index JSON mapping counterparty_id → last_activity_at + channels list.

        Example: ``USER-abc/conversations/index.json``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/conversations/index.json"

    @staticmethod
    def conversation_counterparty_dir(user_id: str, counterparty_id: str) -> str:
        """Prefix for all threads with a given counterparty.

        Example: ``USER-abc/conversations/RES-bob/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        counterparty = StoragePaths._validate_segment(counterparty_id, "counterparty_id")
        return f"{user}/conversations/{counterparty}/"

    @staticmethod
    def conversation_legacy_archive(user_id: str) -> str:
        """Archived legacy chat/history.json after migration.

        Example: ``USER-abc/conversations/.legacy/chat-history.json.archived``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        return f"{user}/conversations/.legacy/chat-history.json.archived"

    # ------------------------------------------------------------------
    # Wave 1 — Policy files (FR-STORE-002)
    # ------------------------------------------------------------------

    @staticmethod
    def agent_policy(user_id: str, agent_id: str, policy_name: str) -> str:
        """Per-user per-agent policy markdown file with YAML frontmatter.

        Example: ``USER-abc/agents/main/policies/delegation.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        policy = StoragePaths._validate_segment(policy_name, "policy_name")
        return f"{user}/agents/{agent}/policies/{policy}.md"

    @staticmethod
    def agent_policies_prefix(user_id: str, agent_id: str) -> str:
        """Prefix to list all policy files for a user+agent pair.

        Example: ``USER-abc/agents/main/policies/``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/policies/"

    @staticmethod
    def outbound_profile(user_id: str, agent_id: str) -> str:
        """Per-user outbound communication agent profile (FR-OUT-001).

        Example: ``USER-abc/agents/main/outbound_profile.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/outbound_profile.md"

    @staticmethod
    def working_context(user_id: str, agent_id: str = "main") -> str:
        """Working memory context file for the agent.

        Example: ``USER-abc/agents/main/working/context.md``
        """
        user = StoragePaths._validate_segment(user_id, "user_id")
        agent = StoragePaths._validate_segment(agent_id, "agent_id")
        return f"{user}/agents/{agent}/working/context.md"


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


class UserWriteScopedStorageClient(StorageClient):
    """Storage proxy that restricts writes/deletes to one user's prefix.

    Reads are intentionally unrestricted so orchestrator callers can continue
    loading system-level prompt assets (e.g. ``system/prompts/...``), while
    all mutating operations are fail-closed to ``{user_id}/``.
    """

    def __init__(self, base: StorageClient, user_id: str) -> None:
        self._base = base
        self._user_id = StoragePaths._validate_segment(user_id, "user_id")
        self._user_root = StoragePaths.user_root(self._user_id)

    @staticmethod
    def _normalize(path: str) -> str:
        normalized = str(path).strip().replace("\\", "/")
        if not normalized:
            raise ValueError("path cannot be empty")
        return normalized

    def _assert_write_scope(self, path: str) -> str:
        normalized = self._normalize(path)
        if normalized.startswith(self._user_root):
            return normalized

        logger.warning(
            "storage.scope_write_denied",
            extra={
                "event_type": "storage.scope_write_denied",
                "user_id": self._user_id,
                "path": normalized,
                "allowed_prefix": self._user_root,
            },
        )
        raise PermissionError(
            f"Write denied for path '{normalized}'; allowed prefix is '{self._user_root}'."
        )

    async def read(self, path: str) -> bytes:
        return await self._base.read(self._normalize(path))

    async def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "text/plain",
    ) -> None:
        allowed = self._assert_write_scope(path)
        await self._base.write(allowed, data, content_type)

    async def delete(self, path: str) -> None:
        allowed = self._assert_write_scope(path)
        await self._base.delete(allowed)

    async def list_objects(self, prefix: str) -> list[str]:
        return await self._base.list_objects(self._normalize(prefix))

    async def exists(self, path: str) -> bool:
        return await self._base.exists(self._normalize(path))


class S3StorageClient(StorageClient):
    """boto3-backed StorageClient supporting AWS S3 and MinIO.

    Args:
        bucket: Name of the S3 bucket to operate on.
        endpoint_url: Override for MinIO or other S3-compatible servers.
            Leave as None to use the default AWS endpoint.
        region: AWS region name (default ``"us-east-1"``).
        principal_name: The storage principal this client is bound to.
            Logged with every S3 operation for audit purposes (Wave 0 NFR-008).
            Defaults to ``"agent_principal"``.
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        principal_name: str = "agent_principal",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._client: object | None = None  # lazy init
        self._principal_name = principal_name

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
