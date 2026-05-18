# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.inbound.extractor — Status signal extraction from message text.

Description
-----------
``StatusExtractor`` analyses the plain-text body of an inbound message and
returns a ``StatusExtraction`` describing the most likely status signal it
conveys. Detection is performed via compiled regex patterns grouped by
``StatusSignal`` value; the signal with the highest total keyword match count
wins. Confidence is proportional to match count.

Design Patterns
---------------
- Strategy (Keyword Matching): Each ``StatusSignal`` is associated with a list
  of regex patterns. The extractor iterates all signals, accumulates per-signal
  match counts, and selects the highest-scoring winner. New signals can be added
  by extending ``SIGNAL_PATTERNS`` without changing the algorithm.
- Pure Function: ``extract`` is a synchronous, side-effect-free method that
  takes a string and returns a value object, making it trivially testable and
  safe to call from any context.

Public API
----------
- StatusExtractor: Extracts status signals from inbound message text.
- StatusExtractor.extract: Synchronous extraction returning a StatusExtraction.
- StatusExtractor.SIGNAL_PATTERNS: Class-level dict mapping signals to regex patterns.
- StatusExtractor.SIGNAL_TO_STATE: Class-level dict mapping signals to TaskState.

Dependencies
------------
- re: Compiled regex patterns for keyword matching.
- graphclaw.inbound.models: StatusExtraction, StatusSignal.
- graphclaw.models.enums: ConfidenceLevel, TaskState.

Notes
-----
Patterns are compiled at class definition time so they are shared across all
``StatusExtractor`` instances. All matching is performed on lower-cased text
to avoid case-sensitivity issues without requiring case-insensitive flags on
every pattern.
"""

from __future__ import annotations

import re

from graphclaw.inbound.models import StatusExtraction, StatusSignal
from graphclaw.models.enums import ConfidenceLevel, TaskState

# ---------------------------------------------------------------------------
# Compile all signal patterns once at import time
# ---------------------------------------------------------------------------

# Raw pattern strings per signal — compiled below into _COMPILED_PATTERNS.
_RAW_PATTERNS: dict[StatusSignal, list[str]] = {
    StatusSignal.DONE: [
        r"\b(?:done|completed?|finished|resolved|closed|shipped|delivered)\b",
        r"\b(?:all\s+set|wrapped\s+up|taken\s+care\s+of)\b",
    ],
    StatusSignal.IN_PROGRESS: [
        r"\b(?:working\s+on|in\s+progress|started|underway|ongoing)\b",
        r"\b(?:making\s+progress|halfway|almost)\b",
    ],
    StatusSignal.BLOCKED: [
        r"\b(?:blocked|stuck|waiting\s+(?:on|for)|can'?t\s+proceed)\b",
        r"\b(?:dependency|blocker|impediment)\b",
    ],
    StatusSignal.DELAYED: [
        r"\b(?:delayed|postponed|pushed\s+back|behind\s+schedule)\b",
        r"\b(?:running\s+late|won'?t\s+make\s+it|need\s+more\s+time)\b",
    ],
    StatusSignal.NEEDS_HELP: [
        r"\b(?:help|assist|support|guidance|question|confused)\b",
        r"\b(?:need\s+help|can\s+you|how\s+do\s+I)\b",
    ],
}

# Compile all patterns once.
_COMPILED_PATTERNS: dict[StatusSignal, list[re.Pattern[str]]] = {
    signal: [re.compile(pat) for pat in pats] for signal, pats in _RAW_PATTERNS.items()
}


class StatusExtractor:
    """Extracts the dominant status signal from inbound message text.

    Uses keyword matching via pre-compiled regex patterns. The signal whose
    patterns accumulate the most total matches in the lower-cased message text
    is returned as the best match. Confidence is:

    - ``HIGH``   — 3 or more total keyword matches.
    - ``MEDIUM`` — 1 or 2 total keyword matches.
    - ``LOW``    — 0 matches (signal falls back to INFO_ONLY or UNKNOWN).

    Class Attributes
    ----------------
    SIGNAL_PATTERNS:
        Mapping of ``StatusSignal`` → list of compiled regex patterns. Exposed
        as a class attribute so tests can inspect or extend patterns.
    SIGNAL_TO_STATE:
        Mapping of ``StatusSignal`` → suggested ``TaskState`` transition.
        Signals that do not imply a state change (``INFO_ONLY``, ``UNKNOWN``)
        are absent from this mapping.
    """

    SIGNAL_PATTERNS: dict[StatusSignal, list[re.Pattern[str]]] = _COMPILED_PATTERNS

    SIGNAL_TO_STATE: dict[StatusSignal, TaskState] = {
        StatusSignal.DONE: TaskState.COMPLETE,
        StatusSignal.IN_PROGRESS: TaskState.IN_PROGRESS,
        StatusSignal.BLOCKED: TaskState.BLOCKED,
        StatusSignal.DELAYED: TaskState.DELAYED,
        StatusSignal.NEEDS_HELP: TaskState.BLOCKED,
    }

    def extract(self, text: str) -> StatusExtraction:
        """Extract the most likely status signal from *text*.

        Scans the lower-cased text against all signal patterns, selects the
        signal with the highest keyword match count, and builds a
        ``StatusExtraction`` with an appropriate confidence level.

        Args:
            text: Plain-text message body to analyse.

        Returns:
            A ``StatusExtraction`` with the dominant signal, confidence,
            a short summary (first sentence, max 100 chars), and the
            suggested ``TaskState`` transition (or ``None``).
        """
        text_lower = text.lower()

        best_signal = StatusSignal.UNKNOWN
        best_count = 0

        for signal, patterns in self.SIGNAL_PATTERNS.items():
            count = sum(len(pat.findall(text_lower)) for pat in patterns)
            if count > best_count:
                best_count = count
                best_signal = signal

        # If nothing matched but we have non-empty text, treat as INFO_ONLY.
        if best_signal == StatusSignal.UNKNOWN and text.strip():
            best_signal = StatusSignal.INFO_ONLY

        # Assign confidence based on total match count.
        if best_count >= 3:
            confidence = ConfidenceLevel.HIGH
        elif best_count >= 1:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        suggested_state = self.SIGNAL_TO_STATE.get(best_signal)

        # Build a brief summary from the first sentence (max 100 chars).
        summary = text.split(".")[0][:100].strip()

        return StatusExtraction(
            signal=best_signal,
            confidence=confidence,
            summary=summary,
            suggested_state=suggested_state,
        )
