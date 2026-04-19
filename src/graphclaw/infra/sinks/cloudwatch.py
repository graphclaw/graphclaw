"""CloudWatch sink for GraphClaw structured logs."""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime

from graphclaw.infra.sinks.base import LogEntry, LogSink
from graphclaw.infra.sinks.formatting import format_entry


class CloudWatchSink(LogSink):
    """Best-effort CloudWatch Logs sink using watchtower handlers."""

    def __init__(
        self,
        region: str = "us-east-1",
        log_group_prefix: str = "/graphclaw",
        log_format: str = "jsonl",
        hostname: str | None = None,
    ) -> None:
        self._region = region
        self._prefix = log_group_prefix.rstrip("/")
        self._log_format = "pipe" if log_format == "pipe" else "jsonl"
        self._hostname = hostname or socket.gethostname()
        self._enabled = False
        self._handlers: dict[tuple[str, str], tuple[logging.Logger, logging.Handler]] = {}
        self._boto3_client: object | None = None
        self._watchtower: object | None = None

    @property
    def name(self) -> str:
        return "cloudwatch"

    async def start(self) -> None:
        try:
            import boto3
            import watchtower

            self._boto3_client = boto3.client("logs", region_name=self._region)
            self._watchtower = watchtower
            self._enabled = True
        except Exception:
            # watchtower is optional and may be unavailable in local environments.
            self._enabled = False

    async def stop(self) -> None:
        if not self._enabled:
            return

        try:
            await asyncio.to_thread(self._close_handlers)
        except Exception:
            pass
        finally:
            self._handlers.clear()

    async def write_batch(self, entries: list[LogEntry]) -> None:
        if not self._enabled or not entries:
            return

        try:
            await asyncio.to_thread(self._write_batch_sync, entries)
        except Exception:
            # CloudWatch issues should never break request processing.
            pass

    def _write_batch_sync(self, entries: list[LogEntry]) -> None:
        for entry in entries:
            message = format_entry(entry, self._log_format)
            for log_group, stream_name in self._targets(entry):
                logger = self._get_or_create_logger(log_group, stream_name)
                if logger is not None:
                    logger.info(message)

    def _targets(self, entry: LogEntry) -> list[tuple[str, str]]:
        service = str(entry.get("service") or "platform")
        level = str(entry.get("level") or "INFO").upper()
        event_type = str(entry.get("event_type") or "")
        user_id = str(entry.get("user_id") or "SYSTEM")
        if user_id in {"", "-"}:
            user_id = "SYSTEM"

        date_prefix = self._date_prefix(entry)
        session_id = str(entry.get("session_id") or "-")

        targets: list[tuple[str, str]] = []

        if service == "gateway":
            targets.append((f"{self._prefix}/channel-gateway", f"{date_prefix}/{self._hostname}"))
        elif service == "trigger-engine":
            targets.append((f"{self._prefix}/trigger-engine", f"{date_prefix}/{self._hostname}"))
        elif service == "agent-runtime":
            targets.append(
                (f"{self._prefix}/agent-runtime/{user_id}", f"{date_prefix}/{session_id}")
            )
        elif service == "skill-agents":
            targets.append(
                (f"{self._prefix}/skill-agents/{user_id}", f"{date_prefix}/{session_id}")
            )
        else:
            targets.append(
                (f"{self._prefix}/platform/{service}", f"{date_prefix}/{self._hostname}")
            )

        if level in {"ERROR", "CRITICAL"}:
            targets.append((f"{self._prefix}/platform/errors", f"{date_prefix}/{service}"))

        if event_type.startswith("audit."):
            targets.append((f"{self._prefix}/platform/audit", f"{date_prefix}/{service}"))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for target in targets:
            if target not in seen:
                deduped.append(target)
                seen.add(target)
        return deduped

    def _date_prefix(self, entry: LogEntry) -> str:
        raw = str(entry.get("timestamp") or "")
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return ts.strftime("%Y/%m/%d")
        except ValueError:
            return datetime.utcnow().strftime("%Y/%m/%d")

    def _get_or_create_logger(self, log_group: str, stream_name: str) -> logging.Logger | None:
        key = (log_group, stream_name)
        if key in self._handlers:
            return self._handlers[key][0]

        if self._watchtower is None:
            return None

        try:
            logger = logging.getLogger(f"graphclaw.cloudwatch.{log_group}.{stream_name}")
            logger.setLevel(logging.INFO)
            logger.propagate = False

            handler = self._watchtower.CloudWatchLogHandler(
                log_group_name=log_group,
                log_stream_name=stream_name,
                boto3_client=self._boto3_client,
                create_log_group=True,
                create_log_stream=True,
            )
            logger.addHandler(handler)
            self._handlers[key] = (logger, handler)
            return logger
        except Exception:
            return None

    def _close_handlers(self) -> None:
        for logger, handler in self._handlers.values():
            try:
                logger.removeHandler(handler)
                handler.close()
            except Exception:
                continue
