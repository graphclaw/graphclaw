"""Global pytest configuration for GraphClaw tests.

Configures the asyncio event loop policy for Windows compatibility
with psycopg async connections (psycopg requires SelectorEventLoop).
"""

from __future__ import annotations

import asyncio
import sys

import pytest

# Force SelectorEventLoop globally on Windows before any async test runs.
# psycopg cannot use ProactorEventLoop (the default on Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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
