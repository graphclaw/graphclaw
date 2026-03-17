"""Configuration management for GraphClaw.

Loads settings from environment variables (with optional .env file support).
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

    dsn: str = field(
        default_factory=lambda: os.environ["DATABASE_URL"]
    )
    min_pool_size: int = field(
        default_factory=lambda: int(os.getenv("DB_POOL_MIN", "2"))
    )
    max_pool_size: int = field(
        default_factory=lambda: int(os.getenv("DB_POOL_MAX", "10"))
    )
    graph_name: str = field(
        default_factory=lambda: os.getenv("AGE_GRAPH_NAME", "graphclaw")
    )


@dataclass(frozen=True)
class AppConfig:
    """Application-level configuration."""

    secrets_backend: str = field(
        default_factory=lambda: os.getenv("SECRETS_BACKEND", "env_file")
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    environment: str = field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "development")
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


#: Module-level singleton — import this in other modules.
config = Config()
