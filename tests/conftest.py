# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Global pytest configuration for GraphClaw tests.

Configures the asyncio event loop policy for Windows compatibility
with psycopg async connections (psycopg requires SelectorEventLoop).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from tests.integration_precheck import run_services_precheck


def _configure_local_test_defaults() -> None:
    """Set consistent local defaults for integration-capable test runs.

    These values align with ``docker/docker-compose.yml`` defaults and prevent
    per-module fallback divergence (for example ``minioadmin`` vs ``graphclaw``).
    """
    os.environ.setdefault(
        "TEST_DATABASE_URL", "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw"
    )
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
    os.environ.setdefault("STORAGE_ENDPOINT_URL", "http://localhost:9000")
    os.environ.setdefault("STORAGE_BUCKET", "graphclaw")
    os.environ.setdefault("STORAGE_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "graphclaw")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "graphclaw_dev")


_configure_local_test_defaults()

# Force SelectorEventLoop globally on Windows before any async test runs.
# psycopg cannot use ProactorEventLoop (the default on Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add explicit integration-test controls."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.integration after service precheck.",
    )
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="Run agent eval tests (requires ANTHROPIC_API_KEY and live orchestrator).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used in this repository."""
    config.addinivalue_line(
        "markers",
        "integration: tests that require live DB/Redis/MinIO services",
    )
    config.addinivalue_line(
        "markers",
        "agent_eval: agent behavioral eval tests — gated by --run-evals",
    )
    config.addinivalue_line(
        "markers",
        "eval_canary: cheap canary subset of agent_evals that runs on relevant PRs",
    )
    config.addinivalue_line(
        "markers",
        "slow: tests expected to take more than 10 seconds",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Gate integration tests behind a mandatory services-up precheck."""
    integration_items = [item for item in items if "integration" in item.keywords]
    if not integration_items:
        return

    run_integration = config.getoption("--run-integration") or os.getenv(
        "GRAPHCLAW_RUN_INTEGRATION", "0"
    ) in {"1", "true", "TRUE", "yes", "YES"}

    if not run_integration:
        skip = pytest.mark.skip(
            reason=(
                "integration test skipped: pass --run-integration (or set "
                "GRAPHCLAW_RUN_INTEGRATION=1) to run after services precheck"
            )
        )
        for item in integration_items:
            item.add_marker(skip)
        return

    ok, details = run_services_precheck()
    if ok:
        return

    detail_text = "\n".join(f"- {entry}" for entry in details)
    raise pytest.UsageError(
        "Integration services precheck failed.\n"
        "Run/verify local services before re-running integration tests:\n"
        f"{detail_text}"
    )


@pytest.fixture(scope="session")
def event_loop():
    """Override the default event loop for the entire session.

    This ensures the SelectorEventLoop is used for session-scoped
    async fixtures like the DB pool.
    """
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()
