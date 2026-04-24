"""graphclaw.infra.logging.handlers.stdout — StdoutJsonHandler."""

from __future__ import annotations

import logging
import sys


class StdoutJsonHandler(logging.StreamHandler):
    """Writes formatted log records to stdout.

    Uses JsonFormatter to produce JSONL output. Configured with a minimum
    level so DEBUG records can be filtered to stdout while still reaching
    durable sinks at INFO+.
    """

    def __init__(self, min_level: str = "DEBUG") -> None:
        super().__init__(stream=sys.stdout)
        self.setLevel(getattr(logging, min_level.upper(), logging.DEBUG))
