# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.inbound — Inbound Update Protocol for GraphClaw.

Description
-----------
The inbound subsystem processes messages arriving from external channels
(email, API, CLI) and resolves them to tasks in the property graph. It
extracts status signals, determines follow-up actions, and publishes state
updates to the message broker.

Design Patterns
---------------
- Pipeline: Messages flow through resolver → extractor → processor in a
  clearly defined sequence with each stage producing a typed result object.
- Facade: This package exposes a curated public API so callers import from
  ``graphclaw.inbound`` rather than individual sub-modules.

Public API
----------
- StatusSignal: Enum of extractable status signals from message text.
- TaskResolution: Result of resolving an inbound message to a graph task.
- StatusExtraction: Extracted status information from message text.
- InboundResult: Complete result of processing a single inbound message.
- TaskResolver: Resolves messages to tasks via ID lookup and vector search.
- StatusExtractor: Extracts status signals from message text via keyword matching.
- InboundProcessor: Orchestrates the full inbound processing pipeline.

Dependencies
------------
- graphclaw.inbound.models: Domain model classes.
- graphclaw.inbound.resolver: TaskResolver implementation.
- graphclaw.inbound.extractor: StatusExtractor implementation.
- graphclaw.inbound.processor: InboundProcessor implementation.

Notes
-----
``InboundProcessor`` is the primary entry point for callers. Inject a
``TaskResolver``, ``StatusExtractor``, and optional ``MessageBroker`` /
``AsyncLogger`` to configure the pipeline.
"""

from __future__ import annotations

from graphclaw.inbound.extractor import StatusExtractor
from graphclaw.inbound.models import (
    InboundResult,
    StatusExtraction,
    StatusSignal,
    TaskResolution,
)
from graphclaw.inbound.processor import InboundProcessor
from graphclaw.inbound.resolver import TaskResolver

__all__ = [
    "StatusSignal",
    "TaskResolution",
    "StatusExtraction",
    "InboundResult",
    "TaskResolver",
    "StatusExtractor",
    "InboundProcessor",
]
