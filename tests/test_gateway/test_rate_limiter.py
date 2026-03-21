from __future__ import annotations
"""Tests for graphclaw.gateway.rate_limiter — rate limiting middleware.

Covers:
- RATE_LIMITS config key presence and value relationships.
- RateLimiter.is_allowed logic (under limit, over limit, remaining never negative).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from graphclaw.gateway.rate_limiter import RATE_LIMITS, RateLimiter


# ---------------------------------------------------------------------------
# RATE_LIMITS config tests
# ---------------------------------------------------------------------------


def test_rate_limits_config_keys() -> None:
    """RATE_LIMITS must contain all four category keys."""
    assert "unauthenticated" in RATE_LIMITS
    assert "authenticated" in RATE_LIMITS
    assert "a2a" in RATE_LIMITS
    assert "webhook" in RATE_LIMITS


def test_authenticated_limit_higher_than_unauthenticated() -> None:
    """Authenticated users must have a higher limit than unauthenticated IPs."""
    auth_limit, _ = RATE_LIMITS["authenticated"]
    unauth_limit, _ = RATE_LIMITS["unauthenticated"]
    assert auth_limit > unauth_limit


# ---------------------------------------------------------------------------
# RateLimiter.is_allowed tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def rate_limiter() -> RateLimiter:
    return RateLimiter(redis_url="redis://localhost:6379/0")


def _make_mock_pipeline(zcard_result: int) -> MagicMock:
    """Return a mock Redis pipeline whose execute() returns a plausible result list."""
    pipeline = MagicMock()
    pipeline.zremrangebyscore = MagicMock()
    pipeline.zadd = MagicMock()
    pipeline.zcard = MagicMock()
    pipeline.expire = MagicMock()
    # execute returns [zremrangebyscore result, zadd result, zcard result, expire result]
    pipeline.execute = AsyncMock(return_value=[0, 1, zcard_result, True])
    return pipeline


@pytest.mark.asyncio
async def test_is_allowed_under_limit(rate_limiter: RateLimiter) -> None:
    """Request count (5) under limit (30) → allowed=True, remaining=25."""
    mock_client = MagicMock()
    mock_pipeline = _make_mock_pipeline(zcard_result=5)
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch.object(rate_limiter, "_get_client", AsyncMock(return_value=mock_client)):
        allowed, remaining = await rate_limiter.is_allowed("ip:1.2.3.4", limit=30, window_seconds=60)

    assert allowed is True
    assert remaining == 25


@pytest.mark.asyncio
async def test_is_allowed_over_limit(rate_limiter: RateLimiter) -> None:
    """Request count (35) over limit (30) → allowed=False, remaining=0."""
    mock_client = MagicMock()
    mock_pipeline = _make_mock_pipeline(zcard_result=35)
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch.object(rate_limiter, "_get_client", AsyncMock(return_value=mock_client)):
        allowed, remaining = await rate_limiter.is_allowed("ip:1.2.3.4", limit=30, window_seconds=60)

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_remaining_never_negative(rate_limiter: RateLimiter) -> None:
    """Remaining should be clamped at 0, never negative (count=50, limit=30 → remaining=0)."""
    mock_client = MagicMock()
    mock_pipeline = _make_mock_pipeline(zcard_result=50)
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch.object(rate_limiter, "_get_client", AsyncMock(return_value=mock_client)):
        allowed, remaining = await rate_limiter.is_allowed("ip:1.2.3.4", limit=30, window_seconds=60)

    assert allowed is False
    assert remaining == 0
    assert remaining >= 0
