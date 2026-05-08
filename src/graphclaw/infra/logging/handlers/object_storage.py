# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.logging.handlers.object_storage — ObjectStorageHandler.

Writes batched log records to S3/MinIO using synchronous boto3 called directly
from the QueueListener's dedicated OS thread — no asyncio boundary crossing.

Batching: flush when batch_size records accumulated OR flush_interval seconds
elapsed since last flush. Final flush on close() for graceful shutdown.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class ObjectStorageHandler(logging.Handler):
    """Buffers log records and writes them to S3/MinIO in batches.

    emit() is called synchronously by QueueListener's thread. boto3 is used
    directly with its synchronous API — correct and safe in a non-event-loop
    thread.

    Args:
        bucket: S3/MinIO bucket name.
        endpoint_url: Override endpoint for MinIO (None for AWS S3).
        region: AWS/MinIO region.
        min_level: Minimum level to persist (default INFO).
        batch_size: Flush after this many records (default 50).
        flush_interval: Flush after this many seconds (default 30.0).
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        min_level: str = "INFO",
        batch_size: int = 50,
        flush_interval: float = 30.0,
    ) -> None:
        super().__init__()
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._min_level_no = getattr(logging, min_level.upper(), logging.INFO)
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        self._buffer: list[logging.LogRecord] = []
        self._lock = threading.Lock()
        self._last_flush: float = time.monotonic()
        self._boto3_client: Any = None
        # Include a process-local suffix in each hourly file name to avoid
        # cross-process read-modify-write collisions on the same object key.
        self._path_suffix = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"

    def _get_client(self) -> Any:
        if self._boto3_client is None:
            import boto3

            kwargs: dict[str, Any] = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            self._boto3_client = boto3.client("s3", **kwargs)
        return self._boto3_client

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < self._min_level_no:
            return
        with self._lock:
            self._buffer.append(record)
            elapsed = time.monotonic() - self._last_flush
            should_flush = len(self._buffer) >= self._batch_size or elapsed >= self._flush_interval
        if should_flush:
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            batch, self._buffer = self._buffer, []
            self._last_flush = time.monotonic()

        grouped: dict[str, list[str]] = {}
        for record in batch:
            line = self.format(record) if self.formatter else record.getMessage()
            path = self._compute_path(record)
            grouped.setdefault(path, []).append(line)

        client = self._get_client()
        for path, lines in grouped.items():
            self._append_to_s3(client, path, lines)

    def _append_to_s3(self, client: Any, path: str, lines: list[str]) -> None:
        """Read-modify-write to S3 (MinIO does not support append)."""
        try:
            response = client.get_object(Bucket=self._bucket, Key=path)
            existing = response["Body"].read().decode("utf-8")
        except Exception:
            existing = ""

        new_content = "\n".join(lines)
        if existing and not existing.endswith("\n"):
            existing += "\n"
        merged = (existing + new_content + "\n").encode("utf-8")

        try:
            client.put_object(
                Bucket=self._bucket,
                Key=path,
                Body=merged,
                ContentType="application/x-ndjson",
            )
        except Exception:
            self.handleError(None)  # type: ignore[arg-type]

    def _compute_path(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        hour_key = ts.strftime("%Y-%m-%d/%H00Z")
        file_name = f"{hour_key}-{self._path_suffix}.jsonl"
        service = str(getattr(record, "service", "platform"))
        user_id = str(getattr(record, "user_id", "") or "").strip()
        if user_id and user_id not in {"-", "SYSTEM", ""}:
            return f"{user_id}/logs/{service}/{file_name}"
        return f"system/logs/{service}/{file_name}"

    def close(self) -> None:
        self._flush()
        super().close()
