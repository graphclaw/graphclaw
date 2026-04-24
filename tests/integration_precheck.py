"""Service readiness precheck helpers for integration test execution."""

from __future__ import annotations

import os
from collections.abc import Sequence

import boto3
import psycopg
import redis


def _check_database(dsn: str) -> str | None:
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return None
    except Exception as exc:  # noqa: BLE001
        return f"database not ready ({dsn}): {exc}"


def _check_redis(redis_url: str) -> str | None:
    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        if client.ping() is not True:
            return f"redis ping failed ({redis_url})"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"redis not ready ({redis_url}): {exc}"


def _check_storage(
    endpoint_url: str,
    bucket: str,
    region: str,
    access_key: str,
    secret_key: str,
) -> str | None:
    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        client.head_bucket(Bucket=bucket)
        return None
    except Exception as exc:  # noqa: BLE001
        return f"storage not ready ({endpoint_url}, bucket={bucket}): {exc}"


def run_services_precheck() -> tuple[bool, list[str]]:
    """Validate DB, Redis, and object storage availability for integration tests."""
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
    )
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    endpoint_url = os.getenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
    bucket = os.getenv("STORAGE_BUCKET", "graphclaw")
    region = os.getenv("STORAGE_REGION", "us-east-1")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "graphclaw")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "graphclaw_dev")

    checks: Sequence[str | None] = (
        _check_database(dsn),
        _check_redis(redis_url),
        _check_storage(endpoint_url, bucket, region, access_key, secret_key),
    )
    failures = [entry for entry in checks if entry is not None]
    return len(failures) == 0, failures
