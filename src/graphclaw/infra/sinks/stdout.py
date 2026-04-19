"""Stdout sink for GraphClaw structured logging."""

from __future__ import annotations

import sys

from graphclaw.infra.sinks.base import LogEntry, LogSink
from graphclaw.infra.sinks.formatting import format_entry


class StdoutSink(LogSink):
    """Writes log entries to stdout in pipe or JSONL format."""

    def __init__(self, log_format: str = "jsonl") -> None:
        self._log_format = "pipe" if log_format == "pipe" else "jsonl"

    @property
    def name(self) -> str:
        return "stdout"

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def write_batch(self, entries: list[LogEntry]) -> None:
        if not entries:
            return

        try:
            lines = "\n".join(format_entry(entry, self._log_format) for entry in entries) + "\n"
            sys.stdout.write(lines)
            sys.stdout.flush()
        except Exception:
            # Logging failures must never affect runtime flow.
            pass
