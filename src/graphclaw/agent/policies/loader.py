# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.policies.loader — Load and Redis-cache per-user policy files.

Description
-----------
Reads ``.md`` policy files from MinIO, parses YAML frontmatter into typed Pydantic
schemas, and caches the result in Redis for 15 minutes (same TTL as profile.md).
Cache key: ``policy:{user_id}:{agent_id}:{policy_name}``.

Design Patterns
---------------
- Cache-aside: check Redis first; read from MinIO on miss; write back to Redis.
- Fail-mode: when MinIO read fails and fail_mode is ``closed``, raises PolicyLoadError.
- Version tracking: MD5 of raw file bytes stored alongside parsed result for
  cache invalidation on PUT.

Public API
----------
- PolicyLoader: async load + invalidate.
- PolicyLoadError: raised on load failure when fail_mode=closed.
- LoadedPolicy: result container (schema_obj, body, raw_bytes).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import yaml

from graphclaw.agent.policies.schemas import (
    CANONICAL_POLICY_NAMES,
    POLICY_SCHEMA_MAP,
    FailMode,
)
from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 900  # 15 min


class PolicyLoadError(Exception):
    """Raised when a policy fails to load and fail_mode is 'closed'."""


@dataclass
class LoadedPolicy:
    """Result of loading one policy file."""

    policy_name: str
    schema_obj: Any  # Pydantic model instance
    body: str  # Raw markdown body (after frontmatter)
    raw_bytes: bytes
    etag: str  # MD5 of raw_bytes for cache invalidation


def _parse_frontmatter(raw: bytes) -> tuple[dict, str]:
    """Split YAML frontmatter and markdown body from raw bytes.

    Returns (frontmatter_dict, body_str).  If no frontmatter found, returns
    ({}, full decoded text).
    """
    text = raw.decode("utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"Invalid YAML frontmatter: {exc}") from exc
    return fm, body


class PolicyLoader:
    """Loads and Redis-caches per-user agent policy files.

    Args:
        storage: StorageClient for MinIO reads.
        redis_client: aioredis / redis.asyncio client.  If None, caching is
            skipped (graceful degradation — still loads from MinIO).
    """

    def __init__(self, storage: StorageClient, redis_client: Any | None = None) -> None:
        self._storage = storage
        self._redis = redis_client

    def _cache_key(self, user_id: str, agent_id: str, policy_name: str) -> str:
        return f"policy:{user_id}:{agent_id}:{policy_name}"

    async def load(
        self,
        user_id: str,
        agent_id: str,
        policy_name: str,
    ) -> LoadedPolicy:
        """Load a policy file, using Redis cache if available.

        Parameters
        ----------
        user_id, agent_id:
            Identifies the policy owner + agent.
        policy_name:
            One of CANONICAL_POLICY_NAMES (e.g. ``"delegation"``).

        Returns
        -------
        LoadedPolicy

        Raises
        ------
        PolicyLoadError:
            When the file is missing or malformed and fail_mode is ``closed``.
        """
        schema_cls = POLICY_SCHEMA_MAP.get(policy_name)
        if schema_cls is None:
            raise PolicyLoadError(f"Unknown policy name: {policy_name!r}")

        cache_key = self._cache_key(user_id, agent_id, policy_name)

        # --- Cache check ---
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    data = json.loads(cached)
                    fm = data["frontmatter"]
                    body = data["body"]
                    raw_bytes = data["raw_bytes"].encode("latin-1")
                    schema_obj = schema_cls(**fm)
                    etag = data.get("etag", "")
                    return LoadedPolicy(
                        policy_name=policy_name,
                        schema_obj=schema_obj,
                        body=body,
                        raw_bytes=raw_bytes,
                        etag=etag,
                    )
            except Exception:
                logger.debug("Policy cache miss or error for %s/%s", user_id, policy_name)

        # --- MinIO read ---
        path = StoragePaths.agent_policy(user_id, agent_id, policy_name)
        try:
            raw_bytes = await self._storage.read(path)
        except FileNotFoundError:
            # Fall back to closed default when missing.
            fail_mode = getattr(
                schema_cls.model_fields.get("fail_mode"), "default", FailMode.CLOSED
            )
            if hasattr(fail_mode, "value"):
                fail_mode = fail_mode.value
            if fail_mode == FailMode.CLOSED.value or fail_mode == FailMode.CLOSED:
                raise PolicyLoadError(f"Policy file not found: {path!r} (fail_mode=closed)")
            # Degraded: return schema defaults with empty body.
            logger.warning(
                "Policy %s missing for %s/%s — using defaults", policy_name, user_id, agent_id
            )
            schema_obj = schema_cls()
            return LoadedPolicy(
                policy_name=policy_name,
                schema_obj=schema_obj,
                body="",
                raw_bytes=b"",
                etag="",
            )

        # --- Parse ---
        try:
            fm, body = _parse_frontmatter(raw_bytes)
            schema_obj = schema_cls(**fm)
        except PolicyLoadError:
            raise
        except Exception as exc:
            raise PolicyLoadError(f"Failed to parse policy {policy_name!r}: {exc}") from exc

        etag = hashlib.md5(  # noqa: S324 — non-crypto cache etag
            raw_bytes, usedforsecurity=False
        ).hexdigest()

        # --- Cache write ---
        if self._redis is not None:
            try:
                payload = json.dumps(
                    {
                        "frontmatter": fm,
                        "body": body,
                        "raw_bytes": raw_bytes.decode("latin-1"),
                        "etag": etag,
                    }
                )
                await self._redis.setex(cache_key, _CACHE_TTL_SECONDS, payload)
            except Exception:
                logger.debug("Failed to cache policy %s", cache_key)

        return LoadedPolicy(
            policy_name=policy_name,
            schema_obj=schema_obj,
            body=body,
            raw_bytes=raw_bytes,
            etag=etag,
        )

    async def invalidate(self, user_id: str, agent_id: str, policy_name: str) -> None:
        """Invalidate the Redis cache entry for a policy (call after PUT)."""
        if self._redis is None:
            return
        cache_key = self._cache_key(user_id, agent_id, policy_name)
        try:
            await self._redis.delete(cache_key)
        except Exception:
            logger.debug("Failed to invalidate policy cache %s", cache_key)

    async def load_all(self, user_id: str, agent_id: str) -> dict[str, LoadedPolicy]:
        """Load all canonical policy files for a user+agent, tolerating missing ones.

        Returns a dict keyed by policy_name.  Policies with fail_mode=degraded
        that are missing return schema defaults; closed ones raise PolicyLoadError.
        """
        results: dict[str, LoadedPolicy] = {}
        for name in CANONICAL_POLICY_NAMES:
            try:
                results[name] = await self.load(user_id, agent_id, name)
            except PolicyLoadError:
                raise
        return results
