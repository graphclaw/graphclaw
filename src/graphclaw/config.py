# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.config — Application configuration loaded from environment variables.

Description
-----------
Provides two frozen dataclasses (``DatabaseConfig`` and ``AppConfig``) that
read their values from environment variables at instantiation time, and a
``Config`` singleton that exposes them via ``cached_property`` accessors.
A ``.env`` file is loaded automatically if present, making local development
setup straightforward without modifying the environment.

Design Patterns
---------------
- Singleton: The module-level ``config`` instance is the canonical configuration
  object; all application code imports it rather than constructing their own.
- Frozen Dataclass: Both config classes are immutable after construction, preventing
  accidental runtime mutation of configuration values.

Public API
----------
- DatabaseConfig: Postgres + AGE connection settings.
- AppConfig: Application-level settings (secrets backend, API keys, log level).
- MemoryConfig: Tiered-memory / distillation / context-compaction settings.
- Config: Top-level singleton with ``database``, ``app``, and ``memory`` cached properties.
- config: Module-level ``Config`` instance to import in application code.

Dependencies
------------
- dotenv: Loads ``.env`` file into the environment at import time.
- os: Environment variable access.

Notes
-----
``DATABASE_URL`` is required and will raise ``KeyError`` at construction time if
absent.  All other settings have safe defaults.  The ``SECRETS_BACKEND`` variable
defaults to ``env_file`` for local development (Docker Compose); production
deployments should set it to ``aws_sm`` or ``vault``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import cached_property

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to *default* on missing/invalid."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("config: invalid int for %s=%r; using %d", name, raw, default)
        return default


@dataclass(frozen=True)
class DatabaseConfig:
    """Postgres + AGE connection configuration."""

    dsn: str = field(default_factory=lambda: os.environ["DATABASE_URL"])
    min_pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_MIN", "2")))
    max_pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_MAX", "10")))
    graph_name: str = field(default_factory=lambda: os.getenv("AGE_GRAPH_NAME", "graphclaw"))


@dataclass(frozen=True)
class AppConfig:
    """Application-level configuration."""

    secrets_backend: str = field(default_factory=lambda: os.getenv("SECRETS_BACKEND", "env_file"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    default_llm_provider: str = field(
        default_factory=lambda: os.getenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", "anthropic")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    # Wave 0: No-Delete enforcement feature flag.
    # Defaults to False; flip to True only after all Wave 0 PRs merged + probes green.
    no_delete_enforcement: bool = field(
        default_factory=lambda: (
            os.getenv("GRAPHCLAW_NO_DELETE_ENFORCEMENT", "false").lower() == "true"
        )
    )


@dataclass(frozen=True)
class MemoryConfig:
    """Tiered-memory and context-compaction configuration.

    Single source of truth for every ``GRAPHCLAW_MEMORY_*`` /
    ``GRAPHCLAW_DISTILLATION_*`` setting that governs working/episodic/semantic
    memory, post-turn distillation, and in-context history compression.  All
    fields read from the environment at construction time with production-safe
    defaults, so deployments tune behaviour via env (``.env`` →
    docker-compose ``environment:``) without code changes.
    """

    # Total character budget across all memory tiers; basis for the compaction
    # warning ("consider compacting") surfaced in the system prompt.
    budget_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_BUDGET_CHARS", 80_000)
    )
    # Hard cap on working-memory chars loaded verbatim into the system prompt.
    working_char_cap: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_WORKING_CHAR_CAP", 8_000)
    )
    # Utilisation percentage (of budget_chars) above which the agent is nudged
    # to call estimate_memory / compact_memory.
    compact_threshold_pct: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_COMPACT_THRESHOLD_PCT", 60)
    )
    # ContextManager: number of recent turns kept verbatim before compression.
    window_size: int = field(default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_WINDOW_SIZE", 20))
    # ContextManager: when older-than-window turns exceed this, roll an LLM summary.
    summary_threshold: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_SUMMARY_THRESHOLD", 30)
    )
    # ContextManager: token budget for compressed conversation context.
    budget_tokens: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_BUDGET_TOKENS", 80_000)
    )
    # Max chat-history entries retained in chat/history.json on save.
    history_max: int = field(default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_HISTORY_MAX", 200))
    # LLM model used for post-turn distillation and compaction summaries.
    distillation_model: str = field(
        default_factory=lambda: os.getenv("GRAPHCLAW_DISTILLATION_MODEL", "claude-haiku-4-5")
    )
    # Max chars of each conversation side fed to the distillation prompt.
    distill_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_DISTILL_MAX_CHARS", 1_500)
    )
    # Max words of accumulated node intelligence before trimming.
    intelligence_max_words: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_MEMORY_INTELLIGENCE_MAX_WORDS", 500)
    )


class Config:
    """Top-level configuration singleton.

    Usage::

        from graphclaw.config import config
        pool = await create_pool(config.database.dsn)
    """

    @cached_property
    def database(self) -> DatabaseConfig:
        return DatabaseConfig()

    @cached_property
    def app(self) -> AppConfig:
        return AppConfig()

    @property
    def memory(self) -> MemoryConfig:
        # Plain (uncached) property: re-reads env on each access so deployments
        # and tests can change GRAPHCLAW_MEMORY_* without a process restart.
        # Construction is cheap (a handful of env lookups).
        return MemoryConfig()


#: Module-level singleton — import this in other modules.
config = Config()
