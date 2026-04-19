"""Log sink implementations for AsyncLogger fan-out."""

from __future__ import annotations

from graphclaw.infra.sinks.base import LogEntry, LogSink
from graphclaw.infra.sinks.cloudwatch import CloudWatchSink
from graphclaw.infra.sinks.object_storage import ObjectStorageSink
from graphclaw.infra.sinks.stdout import StdoutSink

__all__ = [
    "LogEntry",
    "LogSink",
    "StdoutSink",
    "ObjectStorageSink",
    "CloudWatchSink",
]
