"""graphclaw.skills — Skill Agent Runtime for GraphClaw.

Description
-----------
Aggregates the public API for the skill execution subsystem: SKILL.md parsing,
async worker pool management, LLM provider routing via LiteLLM, heartbeat
monitoring, all domain models for skill jobs, results, and worker state, and
the Skill Registry v2 for remote/local skill discovery and installation.

Design Patterns
---------------
- Facade: This package exposes a curated public surface from sub-modules so
  callers can import from ``graphclaw.skills`` rather than individual files.
- Dependency Injection: Worker pool, LLM router, and heartbeat monitor accept
  their collaborators at construction time for testability.

Public API
----------
- SkillStatus: Enum of possible skill execution states.
- ThreadState: Enum of worker thread lifecycle states.
- SkillDefinition: Parsed SKILL.md representation.
- SkillJob: A job submitted to the skill worker pool.
- SkillResult: Result from a completed skill execution.
- WorkerStatus: Runtime status snapshot for a single worker.
- HeartbeatConfig: Configuration for the heartbeat protocol.
- SkillParser: Parses SKILL.md files into SkillDefinition objects.
- LLMRouter: Routes LLM calls to providers via LiteLLM.
- SkillWorker: A single async worker that processes SkillJobs.
- WorkerPool: Manages a pool of SkillWorkers with heartbeat-aware dispatch.
- HeartbeatMonitor: Monitors worker heartbeats and respawns dead workers.
- SkillRegistryService: Discovers, installs, and manages skills from
  LOCAL/GITHUB/WEBSITE sources.
- SkillSource: Configuration for a registered skill source.
- SkillSourceType: Enum of supported skill source kinds.
- SkillListing: An available skill discovered from a source index.
- InstalledSkill: Record of an installed skill with usage metrics.
- MarketplaceJson: Parsed marketplace.json payload from a remote source.

Dependencies
------------
- graphclaw.skills.models: All domain model classes and enums.
- graphclaw.skills.parser: SkillParser.
- graphclaw.skills.llm_router: LLMRouter.
- graphclaw.skills.worker: SkillWorker, WorkerPool.
- graphclaw.skills.heartbeat: HeartbeatMonitor.
- graphclaw.skills.registry: SkillRegistryService.
- graphclaw.skills.registry_models: Registry domain dataclasses.

Notes
-----
Import order is intentional: models first (no internal deps), then parser and
llm_router (depend only on models), then worker (depends on models + router),
then heartbeat (depends on worker), then registry (depends on storage and
parser).
"""

from __future__ import annotations

from graphclaw.skills.heartbeat import HeartbeatMonitor
from graphclaw.skills.llm_router import LLMRouter
from graphclaw.skills.models import (
    HeartbeatConfig,
    SkillDefinition,
    SkillJob,
    SkillResult,
    SkillStatus,
    ThreadState,
    WorkerStatus,
)
from graphclaw.skills.parser import SkillParser
from graphclaw.skills.registry import SkillRegistryService
from graphclaw.skills.registry_models import (
    InstalledSkill,
    MarketplaceJson,
    SkillListing,
    SkillSource,
    SkillSourceType,
)
from graphclaw.skills.worker import SkillWorker, WorkerPool

__all__ = [
    # Enums
    "SkillStatus",
    "ThreadState",
    # Models
    "SkillDefinition",
    "SkillJob",
    "SkillResult",
    "WorkerStatus",
    "HeartbeatConfig",
    # Parser
    "SkillParser",
    # LLM
    "LLMRouter",
    # Workers
    "SkillWorker",
    "WorkerPool",
    # Heartbeat
    "HeartbeatMonitor",
    # Registry v2
    "SkillRegistryService",
    "SkillSource",
    "SkillSourceType",
    "SkillListing",
    "InstalledSkill",
    "MarketplaceJson",
]
