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
- ContextConfig: Prompt budget and per-section caps (``GRAPHCLAW_CONTEXT_*``).
- LLMRoutingConfig: Per-``LLMRole`` provider+model resolution (``GRAPHCLAW_MODEL_*``).
- Config: Top-level singleton with ``database``, ``app``, ``memory``,
  ``context``, and ``llm_routing`` properties.
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
import re
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
    # Ollama local LLM configuration for cost-free development
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
    )
    ollama_default_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.2")
    )
    # LiteLLM default model (supports any provider via model prefix: anthropic/, openai/, ollama/, etc.)
    litellm_default_model: str = field(
        default_factory=lambda: os.getenv("LITELLM_DEFAULT_MODEL", "claude-sonnet-4-20250514")
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


@dataclass(frozen=True)
class ContextConfig:
    """Prompt budget and per-section caps (``GRAPHCLAW_CONTEXT_*``).

    Single source of truth for shaping how much of the orchestrator's and
    sub-agents' context window is spent on system-prompt sections, tool
    schemas, conversation history, and tool-call results. Distinct from
    ``MemoryConfig`` (working/episodic/semantic memory tiers) — this config
    governs the *prompt assembly* budget, not memory persistence. Its
    defaults assume a large hosted context window (200k); local models with
    much smaller windows (e.g. 32k for qwen2.5:7b) must set
    ``GRAPHCLAW_CONTEXT_MODEL_WINDOW_TOKENS`` accordingly, or every budget
    check below is sized for a window the model doesn't have.
    """

    # ── Global prompt budget ───────────────────────────────────────────
    model_window_tokens: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_MODEL_WINDOW_TOKENS", 200_000)
    )
    reserve_output_tokens: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_RESERVE_OUTPUT_TOKENS", 4_096)
    )
    prompt_budget_pct: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_PROMPT_BUDGET_PCT", 70)
    )
    # ── Sub-budgets, as a percentage of the prompt budget ──────────────
    system_budget_pct: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SYSTEM_BUDGET_PCT", 30)
    )
    tools_budget_pct: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_TOOLS_BUDGET_PCT", 20)
    )
    history_budget_pct: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_HISTORY_BUDGET_PCT", 50)
    )
    # ── System-prompt section caps (chars) ─────────────────────────────
    persona_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_PERSONA_MAX_CHARS", 2_000)
    )
    semantic_index_max_topics: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SEMANTIC_INDEX_MAX_TOPICS", 15)
    )
    agent_catalog_max_agents: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_AGENT_CATALOG_MAX_AGENTS", 10)
    )
    graph_summary_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_GRAPH_SUMMARY_MAX_CHARS", 2_000)
    )
    kb_index_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_KB_INDEX_MAX_CHARS", 1_500)
    )
    skills_summary_max_items: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SKILLS_SUMMARY_MAX_ITEMS", 8)
    )
    mcp_summary_max_items: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_MCP_SUMMARY_MAX_ITEMS", 5)
    )
    # ── Tool sets ───────────────────────────────────────────────────────
    max_active_tool_sets: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_MAX_ACTIVE_TOOL_SETS", 2)
    )
    # ── Tool-result truncation / cross-iteration pruning ───────────────
    tool_result_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_TOOL_RESULT_MAX_CHARS", 2_000)
    )
    tool_result_keep_recent: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_TOOL_RESULT_KEEP_RECENT", 3)
    )
    tool_result_digest_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_TOOL_RESULT_DIGEST_CHARS", 300)
    )
    # ── History actually sent to the LLM (distinct from history_max, which
    # governs what is *persisted* in chat/history.json) ────────────────
    history_turns_sent: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_HISTORY_TURNS_SENT", 12)
    )
    # ── Sub-agent context isolation ─────────────────────────────────────
    subagent_episodic_max_entries: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SUBAGENT_EPISODIC_MAX_ENTRIES", 3)
    )
    subagent_episodic_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SUBAGENT_EPISODIC_MAX_CHARS", 4_000)
    )
    subagent_prompt_max_chars: int = field(
        default_factory=lambda: _env_int("GRAPHCLAW_CONTEXT_SUBAGENT_PROMPT_MAX_CHARS", 24_000)
    )

    @property
    def prompt_budget_tokens(self) -> int:
        """Total prompt token budget: (window - reserved output) * pct.

        Single formula so every caller derives the same number — do not
        recompute this inline elsewhere.
        """
        usable = max(0, self.model_window_tokens - self.reserve_output_tokens)
        return int(usable * self.prompt_budget_pct / 100)

    @property
    def system_budget_tokens(self) -> int:
        return int(self.prompt_budget_tokens * self.system_budget_pct / 100)

    @property
    def tools_budget_tokens(self) -> int:
        return int(self.prompt_budget_tokens * self.tools_budget_pct / 100)

    @property
    def history_budget_tokens(self) -> int:
        return int(self.prompt_budget_tokens * self.history_budget_pct / 100)


# ---------------------------------------------------------------------------
# Per-role LLM model routing (GRAPHCLAW_MODEL_*)
# ---------------------------------------------------------------------------

#: Closed set of role keys. Kept as plain strings here (not the LLMRole enum)
#: so this module stays free of any graphclaw.llm import — config.py is a
#: low-level module imported very early, and llm/routing.py imports *this*
#: module's LLMRoutingConfig, so the dependency must run in one direction.
_LLM_ROLE_KEYS: tuple[str, ...] = (
    "orchestrator",
    "subagent",
    "skill",
    "distill",
    "classify",
    "summarize",
)

_LLM_ROLE_MODEL_ENV: dict[str, str] = {
    "orchestrator": "GRAPHCLAW_MODEL_ORCHESTRATOR",
    "subagent": "GRAPHCLAW_MODEL_SUBAGENT",
    "skill": "GRAPHCLAW_MODEL_SKILL",
    "distill": "GRAPHCLAW_MODEL_DISTILL",
    "classify": "GRAPHCLAW_MODEL_CLASSIFY",
    "summarize": "GRAPHCLAW_MODEL_SUMMARIZE",
}

_LLM_ROLE_PROVIDER_ENV: dict[str, str] = {
    "orchestrator": "GRAPHCLAW_MODEL_PROVIDER_ORCHESTRATOR",
    "subagent": "GRAPHCLAW_MODEL_PROVIDER_SUBAGENT",
    "skill": "GRAPHCLAW_MODEL_PROVIDER_SKILL",
    "distill": "GRAPHCLAW_MODEL_PROVIDER_DISTILL",
    "classify": "GRAPHCLAW_MODEL_PROVIDER_CLASSIFY",
    "summarize": "GRAPHCLAW_MODEL_PROVIDER_SUMMARIZE",
}

# Legacy per-feature env vars that predate role-based routing. Still honoured
# so existing deployments keep working: a role falls back to its legacy var
# before falling back to the shared GRAPHCLAW_MODEL_DEFAULT/LITELLM_DEFAULT_MODEL.
_LLM_ROLE_LEGACY_MODEL_ENV: dict[str, tuple[str, ...]] = {
    "distill": ("GRAPHCLAW_DISTILLATION_MODEL",),
    "summarize": ("GRAPHCLAW_DISTILLATION_MODEL",),
    "classify": ("INTELLIGENCE_AGENT_MODEL", "GRAPHCLAW_PROFILE_SYNTHESIS_MODEL"),
}

# Inline "<provider>:<model>" prefix, e.g. "litellm:ollama_chat/qwen2.5:7b",
# lets a single env var pick a provider without a separate _PROVIDER_ var. The
# provider alternation is a fixed, colon-free vocabulary so this never
# misparses an ollama_chat/hosted model string (which never starts with one of
# these words followed by a colon).
_INLINE_PROVIDER_RE = re.compile(r"^(litellm|anthropic|openai):(.+)$")


def _split_inline_provider(value: str) -> tuple[str | None, str]:
    match = _INLINE_PROVIDER_RE.match(value)
    if match:
        return match.group(1), match.group(2)
    return None, value


@dataclass(frozen=True)
class LLMRoutingConfig:
    """Per-role LLM provider+model resolution.

    Model resolution chain per role, first non-empty value wins::

        GRAPHCLAW_MODEL_<ROLE>
          -> legacy per-feature env (distill/summarize/classify only)
          -> GRAPHCLAW_MODEL_DEFAULT
          -> LITELLM_DEFAULT_MODEL

    Provider resolution chain per role::

        inline "<provider>:" prefix on the resolved model string
          -> GRAPHCLAW_MODEL_PROVIDER_<ROLE>
          -> GRAPHCLAW_MODEL_PROVIDER_DEFAULT
          -> caller-supplied default (e.g. the provider selected at gateway
             startup by _select_startup_llm_provider_and_key)

    With only ``LITELLM_DEFAULT_MODEL`` set (today's single-model deployments),
    every role's chain terminates at the same string — role routing is fully
    backward compatible.
    """

    models: dict[str, str] = field(default_factory=dict)
    providers: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> LLMRoutingConfig:
        default_model = os.getenv("GRAPHCLAW_MODEL_DEFAULT", "").strip()
        fallback_model = default_model or os.getenv(
            "LITELLM_DEFAULT_MODEL", "claude-sonnet-4-20250514"
        )
        default_provider_env = os.getenv("GRAPHCLAW_MODEL_PROVIDER_DEFAULT", "").strip() or None

        models: dict[str, str] = {}
        providers: dict[str, str | None] = {}
        for role in _LLM_ROLE_KEYS:
            raw = os.getenv(_LLM_ROLE_MODEL_ENV[role], "").strip()
            if not raw:
                for legacy_env in _LLM_ROLE_LEGACY_MODEL_ENV.get(role, ()):
                    raw = os.getenv(legacy_env, "").strip()
                    if raw:
                        break
            if not raw:
                raw = fallback_model

            inline_provider, model = _split_inline_provider(raw)
            models[role] = model
            providers[role] = (
                inline_provider
                or os.getenv(_LLM_ROLE_PROVIDER_ENV[role], "").strip()
                or default_provider_env
            )
        return cls(models=models, providers=providers)

    def model_for(self, role: str) -> str:
        """Resolved model string for *role* (inline provider prefix already stripped)."""
        return self.models.get(role) or self.models.get("orchestrator", "claude-sonnet-4-20250514")

    def provider_for(self, role: str, *, default: str = "litellm") -> str:
        """Resolved provider for *role*, falling back to *default* when unset."""
        return self.providers.get(role) or default


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

    @property
    def llm_routing(self) -> LLMRoutingConfig:
        # Plain (uncached) property, same rationale as `memory`: role routing
        # must observe env changes within a test process, and construction is
        # a handful of string lookups — cheap enough to redo per access.
        return LLMRoutingConfig.from_env()

    @property
    def context(self) -> ContextConfig:
        # Plain (uncached) property, same rationale as `memory`/`llm_routing`.
        return ContextConfig()


#: Module-level singleton — import this in other modules.
config = Config()
