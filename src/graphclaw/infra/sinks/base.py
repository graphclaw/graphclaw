"""Base interfaces for GraphClaw log sinks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

LogEntry = dict[str, Any]


class LogSink(ABC):
    """Abstract sink contract for AsyncLogger fan-out targets."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable sink name for diagnostics."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize sink resources."""

    @abstractmethod
    async def stop(self) -> None:
        """Flush and release sink resources."""

    @abstractmethod
    async def write_batch(self, entries: list[LogEntry]) -> None:
        """Write a batch of log entries. Implementations must be best-effort."""
