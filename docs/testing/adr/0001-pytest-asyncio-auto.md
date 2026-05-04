# ADR-0001: pytest-asyncio in auto mode

**Status**: Accepted  
**Date**: 2026-05-04

## Decision

Use `pytest-asyncio` with `asyncio_mode = "auto"` in `pyproject.toml`.

## Context

The GraphClaw backend is heavily async (FastAPI, asyncpg, aioredis, aiobotocore). Almost every route handler and service function is a coroutine. Writing `@pytest.mark.asyncio` on every individual test function is mechanical overhead with no engineering benefit.

Windows compatibility requires an additional override: psycopg3 does not support `ProactorEventLoop`; the root `tests/conftest.py` sets `SelectorEventLoop` policy when running on Windows.

## Consequences

- Every `async def test_*` function runs automatically in an event loop — no decorator needed.
- Session-scoped `event_loop` fixture in root `conftest.py` is required for session-scoped async fixtures (e.g., the shared connection pool).
- Mixing sync and async test functions in the same file works without extra configuration.
- Developers new to the project do not need to learn the `@pytest.mark.asyncio` pattern.
