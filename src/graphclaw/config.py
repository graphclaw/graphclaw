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
- Config: Top-level singleton with ``database`` and ``app`` cached properties.
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

import os
from dataclasses import dataclass, field
from functools import cached_property

from dotenv import load_dotenv

load_dotenv()


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
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))


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


#: Module-level singleton — import this in other modules.
config = Config()
