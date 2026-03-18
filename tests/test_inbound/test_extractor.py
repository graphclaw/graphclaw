"""Tests for graphclaw.inbound.extractor — StatusExtractor keyword matching.

Description
-----------
Verifies that each StatusSignal is correctly detected by its keywords,
that INFO_ONLY is returned for non-empty neutral text, that UNKNOWN is
returned for empty text, that confidence escalates with match count, that
SIGNAL_TO_STATE maps are correct, and that summary truncation works.
"""
from __future__ import annotations

import pytest

from graphclaw.inbound.extractor import StatusExtractor
from graphclaw.inbound.models import StatusSignal
from graphclaw.models.enums import ConfidenceLevel, TaskState


@pytest.fixture()
def extractor() -> StatusExtractor:
    return StatusExtractor()


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------


def test_extract_done_signal(extractor: StatusExtractor) -> None:
    """'completed' keyword should yield DONE signal."""
    result = extractor.extract("I've completed the report and it's done.")
    assert result.signal == StatusSignal.DONE


def test_extract_done_shipped(extractor: StatusExtractor) -> None:
    """'shipped' keyword should yield DONE signal."""
    result = extractor.extract("The feature was shipped to production yesterday.")
    assert result.signal == StatusSignal.DONE


def test_extract_in_progress_signal(extractor: StatusExtractor) -> None:
    """'working on' should yield IN_PROGRESS signal."""
    result = extractor.extract("I'm currently working on the integration tests.")
    assert result.signal == StatusSignal.IN_PROGRESS


def test_extract_in_progress_underway(extractor: StatusExtractor) -> None:
    """'underway' keyword should yield IN_PROGRESS signal."""
    result = extractor.extract("The migration is underway and ongoing.")
    assert result.signal == StatusSignal.IN_PROGRESS


def test_extract_blocked_signal(extractor: StatusExtractor) -> None:
    """'blocked' keyword should yield BLOCKED signal."""
    result = extractor.extract("I am blocked waiting on the API credentials.")
    assert result.signal == StatusSignal.BLOCKED


def test_extract_blocked_stuck(extractor: StatusExtractor) -> None:
    """'stuck' keyword should yield BLOCKED signal."""
    result = extractor.extract("Still stuck on the database schema issue.")
    assert result.signal == StatusSignal.BLOCKED


def test_extract_delayed_signal(extractor: StatusExtractor) -> None:
    """'delayed' keyword should yield DELAYED signal."""
    result = extractor.extract("This is going to be delayed by a few days.")
    assert result.signal == StatusSignal.DELAYED


def test_extract_delayed_behind_schedule(extractor: StatusExtractor) -> None:
    """'behind schedule' phrase should yield DELAYED signal."""
    result = extractor.extract("We're behind schedule and need more time.")
    assert result.signal == StatusSignal.DELAYED


def test_extract_needs_help_signal(extractor: StatusExtractor) -> None:
    """'need help' phrase should yield NEEDS_HELP signal."""
    result = extractor.extract("I need help understanding the requirements.")
    assert result.signal == StatusSignal.NEEDS_HELP


def test_extract_needs_help_confused(extractor: StatusExtractor) -> None:
    """'confused' keyword should yield NEEDS_HELP signal."""
    result = extractor.extract("I'm confused about the expected output format.")
    assert result.signal == StatusSignal.NEEDS_HELP


def test_extract_info_only_for_neutral_text(extractor: StatusExtractor) -> None:
    """Non-empty text with no signal keywords should return INFO_ONLY."""
    result = extractor.extract("Just wanted to give you an update on the project timeline.")
    assert result.signal == StatusSignal.INFO_ONLY


def test_extract_unknown_for_empty(extractor: StatusExtractor) -> None:
    """Empty string should return UNKNOWN signal."""
    result = extractor.extract("")
    assert result.signal == StatusSignal.UNKNOWN


def test_extract_unknown_for_whitespace_only(extractor: StatusExtractor) -> None:
    """Whitespace-only text should return UNKNOWN signal."""
    result = extractor.extract("   \t\n")
    assert result.signal == StatusSignal.UNKNOWN


# ---------------------------------------------------------------------------
# Confidence levels
# ---------------------------------------------------------------------------


def test_low_confidence_single_neutral_word(extractor: StatusExtractor) -> None:
    """INFO_ONLY text with no matches → LOW confidence."""
    result = extractor.extract("Hello, just an FYI message.")
    # No signal keyword matches → best_count=0 → LOW
    assert result.confidence == ConfidenceLevel.LOW


def test_medium_confidence_single_keyword(extractor: StatusExtractor) -> None:
    """Single keyword match (best_count=1) → MEDIUM confidence."""
    result = extractor.extract("The task is done.")
    assert result.signal == StatusSignal.DONE
    assert result.confidence == ConfidenceLevel.MEDIUM


def test_high_confidence_multiple_keywords(extractor: StatusExtractor) -> None:
    """Three or more matches → HIGH confidence."""
    result = extractor.extract(
        "I have completed the task, finished all tests, and resolved all issues."
    )
    assert result.signal == StatusSignal.DONE
    assert result.confidence == ConfidenceLevel.HIGH


def test_medium_confidence_two_keywords(extractor: StatusExtractor) -> None:
    """Two keyword matches → MEDIUM confidence."""
    result = extractor.extract("The task is blocked and stuck.")
    assert result.signal == StatusSignal.BLOCKED
    assert result.confidence == ConfidenceLevel.MEDIUM


# ---------------------------------------------------------------------------
# Suggested state mapping
# ---------------------------------------------------------------------------


def test_suggested_state_done(extractor: StatusExtractor) -> None:
    """DONE signal should suggest COMPLETE state."""
    result = extractor.extract("All done.")
    assert result.suggested_state == TaskState.COMPLETE


def test_suggested_state_in_progress(extractor: StatusExtractor) -> None:
    """IN_PROGRESS signal should suggest IN_PROGRESS state."""
    result = extractor.extract("Started working on it, in progress.")
    assert result.suggested_state == TaskState.IN_PROGRESS


def test_suggested_state_blocked(extractor: StatusExtractor) -> None:
    """BLOCKED signal should suggest BLOCKED state."""
    result = extractor.extract("I'm blocked waiting for the dependency.")
    assert result.suggested_state == TaskState.BLOCKED


def test_suggested_state_delayed(extractor: StatusExtractor) -> None:
    """DELAYED signal should suggest DELAYED state."""
    result = extractor.extract("This is delayed and will be postponed.")
    assert result.suggested_state == TaskState.DELAYED


def test_suggested_state_needs_help(extractor: StatusExtractor) -> None:
    """NEEDS_HELP signal should suggest BLOCKED state."""
    result = extractor.extract("I need help with this task.")
    assert result.suggested_state == TaskState.BLOCKED


def test_suggested_state_info_only(extractor: StatusExtractor) -> None:
    """INFO_ONLY signal should have no suggested state."""
    result = extractor.extract("Just a regular update for your information.")
    assert result.signal == StatusSignal.INFO_ONLY
    assert result.suggested_state is None


def test_suggested_state_mapping() -> None:
    """SIGNAL_TO_STATE class attribute should map all action signals."""
    mapping = StatusExtractor.SIGNAL_TO_STATE
    assert mapping[StatusSignal.DONE] == TaskState.COMPLETE
    assert mapping[StatusSignal.IN_PROGRESS] == TaskState.IN_PROGRESS
    assert mapping[StatusSignal.BLOCKED] == TaskState.BLOCKED
    assert mapping[StatusSignal.DELAYED] == TaskState.DELAYED
    assert mapping[StatusSignal.NEEDS_HELP] == TaskState.BLOCKED
    assert StatusSignal.INFO_ONLY not in mapping
    assert StatusSignal.UNKNOWN not in mapping


# ---------------------------------------------------------------------------
# Summary truncation
# ---------------------------------------------------------------------------


def test_summary_from_first_sentence(extractor: StatusExtractor) -> None:
    """Summary should be extracted from text before the first period."""
    result = extractor.extract("Task is done. More details follow here.")
    assert result.summary == "Task is done"


def test_summary_truncation(extractor: StatusExtractor) -> None:
    """Summary must be truncated to 100 characters."""
    long_text = "x" * 200
    result = extractor.extract(long_text)
    assert len(result.summary) <= 100


def test_summary_empty_for_empty_text(extractor: StatusExtractor) -> None:
    """Empty text should produce an empty summary."""
    result = extractor.extract("")
    assert result.summary == ""


def test_summary_stripped(extractor: StatusExtractor) -> None:
    """Summary should be stripped of leading/trailing whitespace."""
    result = extractor.extract("  Task is done  . Other text.")
    assert result.summary == result.summary.strip()
