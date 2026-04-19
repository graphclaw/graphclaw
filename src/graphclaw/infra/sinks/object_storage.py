"""Durable object-storage sink for GraphClaw structured logs."""

from __future__ import annotations

from datetime import datetime

from graphclaw.infra.sinks.base import LogEntry, LogSink
from graphclaw.infra.sinks.formatting import format_entry
from graphclaw.infra.storage import StorageClient, StoragePaths

_LEVEL_PRIORITY = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


class ObjectStorageSink(LogSink):
    """Writes filtered log entries to S3/MinIO in hour-partitioned files."""

    def __init__(
        self,
        storage: StorageClient,
        log_format: str = "jsonl",
        min_level: str = "INFO",
    ) -> None:
        self._storage = storage
        self._log_format = "pipe" if log_format == "pipe" else "jsonl"
        self._min_level = min_level.upper()

    @property
    def name(self) -> str:
        return "object_storage"

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def write_batch(self, entries: list[LogEntry]) -> None:
        try:
            min_priority = _LEVEL_PRIORITY.get(self._min_level, 1)
            filtered = [
                entry
                for entry in entries
                if _LEVEL_PRIORITY.get(str(entry.get("level", "INFO")).upper(), 1) >= min_priority
            ]
            if not filtered:
                return

            grouped: dict[str, list[str]] = {}
            for entry in filtered:
                ts = self._parse_timestamp(entry)
                if ts is None:
                    continue

                hour_key = ts.strftime("%Y-%m-%d/%H00Z")
                service = str(entry.get("service") or "platform")
                user_id = str(entry.get("user_id") or "").strip()
                extension = "jsonl" if self._log_format == "jsonl" else "log"

                if user_id and user_id not in {"-", "SYSTEM"}:
                    path = StoragePaths.user_log_path(
                        user_id, service, hour_key, extension=extension
                    )
                else:
                    path = StoragePaths.system_log_path(service, hour_key, extension=extension)

                grouped.setdefault(path, []).append(format_entry(entry, self._log_format))

            for path, lines in grouped.items():
                await self._append(path, lines)
        except Exception:
            # Sink failures must not crash the logger.
            pass

    def _parse_timestamp(self, entry: LogEntry) -> datetime | None:
        raw = str(entry.get("timestamp") or "")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _append(self, path: str, lines: list[str]) -> None:
        try:
            existing_bytes = await self._storage.read(path)
            existing = existing_bytes.decode("utf-8")
        except FileNotFoundError:
            existing = ""

        new_content = "\n".join(lines)
        if existing and not existing.endswith("\n"):
            existing += "\n"
        merged = existing + new_content + "\n"

        content_type = "application/x-ndjson" if self._log_format == "jsonl" else "text/plain"
        await self._storage.write(path, merged.encode("utf-8"), content_type=content_type)
