"""tests.test_inbound.test_intelligence_agent — Unit tests for InboundIntelligenceAgent.

Description
-----------
Verifies the full intelligence extraction pipeline: LLM call construction,
JSON response parsing (valid and invalid), PII scrubbing, node intelligence
update (prepend + trim), working memory update, action_taken outcomes, and
logger call-through.  All external dependencies (LLM, graph repo, storage)
are mocked.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, invokes ``process()``, and
  asserts on the returned ``IntelligenceUpdate`` and mock call counts.
- Dependency Injection: All collaborators created as AsyncMock instances and
  injected via the constructor.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.inbound.intelligence_agent: InboundIntelligenceAgent, IntelligenceUpdate.
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.inbound.models: InboundResult, TaskResolution.
- graphclaw.models.enums: ConfidenceLevel, MatchedBy.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.gateway.schemas import InboundMessage
from graphclaw.inbound.intelligence_agent import (
    InboundIntelligenceAgent,
    IntelligenceUpdate,
    MAX_INTELLIGENCE_WORDS,
    _scrub_pii,
)
from graphclaw.inbound.models import InboundResult, StatusExtraction, TaskResolution
from graphclaw.models.enums import ConfidenceLevel, MatchedBy, TaskState


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _make_inbound(
    channel: str = "email",
    sender: str = "test@example.com",
    subject: str = "Re: project update",
    body: str = "Deliverable is ready for review.",
    session_id: str = "SES-test-001",
) -> InboundMessage:
    return InboundMessage(
        message_id="msg-001",
        channel=channel,
        sender=sender,
        subject=subject,
        body=body,
        received_at=datetime.now(timezone.utc),
        session_id=session_id,
    )


def _make_resolution_with_task(task_id: str = "TSK-AB-0001-ATM") -> InboundResult:
    resolution = TaskResolution(
        task_id=task_id,
        matched_by=MatchedBy.TASK_ID,
        confidence=ConfidenceLevel.HIGH,
        score=1.0,
        matched_text=task_id,
    )
    status = StatusExtraction(
        signal="DONE",
        suggested_state=TaskState.COMPLETE,
    )
    return InboundResult(
        message_id="msg-001",
        session_id="SES-test-001",
        resolution=resolution,
        status=status,
    )


def _make_resolution_no_match() -> InboundResult:
    resolution = TaskResolution()  # task_id = None
    status = StatusExtraction(signal="UNKNOWN")
    return InboundResult(
        message_id="msg-001",
        session_id="SES-test-001",
        resolution=resolution,
        status=status,
    )


def _make_llm_response(task_entry: str | None, memory_note: str | None) -> MagicMock:
    payload = {"task_entry": task_entry, "memory_note": memory_note}
    response = MagicMock()
    response.content = json.dumps(payload)
    return response


def _make_agent(
    llm_response: object | None = None,
    existing_intelligence: str | None = None,
    existing_context: bytes = b"# Working Context\n## Recent Context\n",
) -> tuple[InboundIntelligenceAgent, AsyncMock, AsyncMock, AsyncMock]:
    """Create an agent with all dependencies mocked."""
    mock_llm = AsyncMock()
    if llm_response is not None:
        mock_llm.complete = AsyncMock(return_value=llm_response)
    else:
        mock_llm.complete = AsyncMock(
            return_value=_make_llm_response("email | inbound | task update received", None)
        )

    mock_repo = AsyncMock()
    mock_repo.get_node_intelligence = AsyncMock(return_value=existing_intelligence)
    mock_repo.update_node_intelligence = AsyncMock()

    mock_storage = AsyncMock()
    mock_storage.read = AsyncMock(return_value=existing_context)
    mock_storage.write = AsyncMock()

    lock = asyncio.Lock()

    agent = InboundIntelligenceAgent(
        llm=mock_llm,
        graph_repo=mock_repo,
        storage=mock_storage,
        memory_lock=lock,
    )
    return agent, mock_llm, mock_repo, mock_storage


# ---------------------------------------------------------------------------
# _scrub_pii helper
# ---------------------------------------------------------------------------


def test_scrub_pii_ssn() -> None:
    assert "[REDACTED-SSN]" in _scrub_pii("SSN is 123-45-6789 ok")


def test_scrub_pii_credit_card() -> None:
    assert "[REDACTED-CC]" in _scrub_pii("Card: 1234 5678 9012 3456")


def test_scrub_pii_phone() -> None:
    assert "[REDACTED-PHONE]" in _scrub_pii("Call me at 555-867-5309")


def test_scrub_pii_no_pii_unchanged() -> None:
    text = "Please review the deliverable before Monday."
    assert _scrub_pii(text) == text


# ---------------------------------------------------------------------------
# process() — node update path
# ---------------------------------------------------------------------------


async def test_process_updates_node_intelligence() -> None:
    """When LLM returns task_entry and task is matched, node intelligence is updated."""
    llm_resp = _make_llm_response("[email | inbound | confirmed upload by EOD]", None)
    agent, _, mock_repo, _ = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0001-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "node_updated"
    assert result.task_intelligence is not None
    mock_repo.update_node_intelligence.assert_called_once()
    call_args = mock_repo.update_node_intelligence.call_args
    # First positional arg is task_id
    assert call_args[0][0] == "TSK-AB-0001-ATM"
    # Updated intelligence starts with the new log line
    updated_text: str = call_args[0][1]
    assert "email | inbound |" in updated_text


async def test_process_prepends_to_existing_intelligence() -> None:
    """New log line is prepended to existing intelligence (newest first)."""
    existing = "[2026-04-11] email | inbound | sent files"
    llm_resp = _make_llm_response("email | inbound | confirmed receipt", None)
    agent, _, mock_repo, _ = _make_agent(
        llm_response=llm_resp, existing_intelligence=existing
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0002-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    updated_text: str = mock_repo.update_node_intelligence.call_args[0][1]
    # New entry should appear before the old entry
    new_pos = updated_text.find("confirmed receipt")
    old_pos = updated_text.find("[2026-04-11]")
    assert new_pos < old_pos


async def test_process_trims_intelligence_over_word_limit() -> None:
    """Intelligence field is trimmed when it exceeds MAX_INTELLIGENCE_WORDS."""
    # Create existing intelligence that is already near the limit
    long_existing = " ".join(["word"] * (MAX_INTELLIGENCE_WORDS - 5))
    llm_resp = _make_llm_response("long update with many words that pushes over limit", None)
    agent, _, mock_repo, _ = _make_agent(
        llm_response=llm_resp, existing_intelligence=long_existing
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0003-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    updated_text: str = mock_repo.update_node_intelligence.call_args[0][1]
    assert "older entries archived" in updated_text


async def test_process_no_update_when_no_task_match() -> None:
    """Node intelligence is NOT updated when no task is matched."""
    llm_resp = _make_llm_response("email | inbound | general message", None)
    agent, _, mock_repo, _ = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    mock_repo.update_node_intelligence.assert_not_called()
    assert result.task_intelligence is None


# ---------------------------------------------------------------------------
# process() — memory update path
# ---------------------------------------------------------------------------


async def test_process_updates_working_memory() -> None:
    """When LLM returns memory_note, working memory is written."""
    llm_resp = _make_llm_response(None, "User prefers concise email responses")
    agent, _, _, mock_storage = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "memory_updated"
    assert result.memory_update == "User prefers concise email responses"
    mock_storage.write.assert_called_once()


async def test_process_appends_under_recent_context_heading() -> None:
    """Memory note is inserted after '## Recent Context' heading."""
    existing = b"# Working Context\n\n## Recent Context\nOld note\n"
    llm_resp = _make_llm_response(None, "New observation about user")
    agent, _, _, mock_storage = _make_agent(
        llm_response=llm_resp, existing_context=existing
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    written: bytes = mock_storage.write.call_args[0][1]
    context = written.decode("utf-8")
    heading_pos = context.find("## Recent Context")
    new_note_pos = context.find("New observation about user")
    assert heading_pos < new_note_pos


async def test_process_creates_recent_context_heading_if_absent() -> None:
    """If '## Recent Context' absent, memory note is appended at end."""
    existing = b"# Working Context\nSome other content\n"
    llm_resp = _make_llm_response(None, "Learned preference")
    agent, _, _, mock_storage = _make_agent(
        llm_response=llm_resp, existing_context=existing
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    written: bytes = mock_storage.write.call_args[0][1]
    assert b"## Recent Context" in written
    assert b"Learned preference" in written


async def test_process_handles_missing_context_file() -> None:
    """When storage.read() raises, a fresh context file is created."""
    llm_resp = _make_llm_response(None, "Fresh note")
    agent, _, _, mock_storage = _make_agent(llm_response=llm_resp)
    mock_storage.read = AsyncMock(side_effect=FileNotFoundError("not found"))

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "memory_updated"
    mock_storage.write.assert_called_once()


# ---------------------------------------------------------------------------
# process() — action_taken combinations
# ---------------------------------------------------------------------------


async def test_process_action_taken_both() -> None:
    """action_taken = 'both' when both node and memory are updated."""
    llm_resp = _make_llm_response(
        "email | inbound | task update confirmed",
        "User responds quickly via email",
    )
    agent, _, _, _ = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0004-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "both"


async def test_process_action_taken_unmatched() -> None:
    """action_taken = 'unmatched' when LLM returns nulls for both fields."""
    llm_resp = _make_llm_response(None, None)
    agent, _, _, _ = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_no_match(),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "unmatched"


# ---------------------------------------------------------------------------
# process() — error handling (invalid LLM JSON)
# ---------------------------------------------------------------------------


async def test_process_invalid_json_sets_error_action() -> None:
    """When LLM returns non-JSON, action_taken = 'error' and node is not updated."""
    bad_response = MagicMock()
    bad_response.content = "This is not JSON at all."
    agent, _, mock_repo, _ = _make_agent(llm_response=bad_response)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0005-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken == "error"
    mock_repo.update_node_intelligence.assert_not_called()


async def test_process_error_action_logged_via_logger() -> None:
    """When JSON parse fails and logger is present, error is logged."""
    bad_response = MagicMock()
    bad_response.content = "not valid json"
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=bad_response)
    mock_repo = AsyncMock()
    mock_repo.get_node_intelligence = AsyncMock(return_value=None)
    mock_repo.update_node_intelligence = AsyncMock()
    mock_storage = AsyncMock()
    mock_storage.read = AsyncMock(return_value=b"# Working Context\n")
    mock_storage.write = AsyncMock()
    mock_logger = AsyncMock()

    agent = InboundIntelligenceAgent(
        llm=mock_llm,
        graph_repo=mock_repo,
        storage=mock_storage,
        memory_lock=asyncio.Lock(),
        logger=mock_logger,
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0006-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    # Two log calls: one for parse_error, one for the final intelligence_update event
    assert mock_logger.log.call_count >= 1
    all_positional = [call[0] for call in mock_logger.log.call_args_list]
    event_types = [args[1] for args in all_positional if len(args) > 1]
    assert "agent.intelligence_parse_error" in event_types


# ---------------------------------------------------------------------------
# process() — PII scrubbing
# ---------------------------------------------------------------------------


async def test_process_scrubs_pii_in_task_entry() -> None:
    """SSN in LLM task_entry output is scrubbed before being stored."""
    llm_resp = _make_llm_response(
        "email | inbound | user mentioned SSN 123-45-6789 in message", None
    )
    agent, _, mock_repo, _ = _make_agent(llm_response=llm_resp)

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0007-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    updated_text: str = mock_repo.update_node_intelligence.call_args[0][1]
    assert "123-45-6789" not in updated_text
    assert "[REDACTED-SSN]" in updated_text


# ---------------------------------------------------------------------------
# process() — logger call-through
# ---------------------------------------------------------------------------


async def test_process_logs_event_when_logger_provided() -> None:
    """AsyncLogger.log() is called once for an 'agent.intelligence_update' event."""
    llm_resp = _make_llm_response("email | inbound | update", None)
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=llm_resp)
    mock_repo = AsyncMock()
    mock_repo.get_node_intelligence = AsyncMock(return_value=None)
    mock_repo.update_node_intelligence = AsyncMock()
    mock_storage = AsyncMock()
    mock_storage.read = AsyncMock(return_value=b"# Working Context\n")
    mock_storage.write = AsyncMock()
    mock_logger = AsyncMock()

    agent = InboundIntelligenceAgent(
        llm=mock_llm,
        graph_repo=mock_repo,
        storage=mock_storage,
        memory_lock=asyncio.Lock(),
        logger=mock_logger,
    )

    await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0008-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    mock_logger.log.assert_called_once()
    call_args = mock_logger.log.call_args[0]
    assert "agent.intelligence_update" in call_args


async def test_process_no_logger_does_not_crash() -> None:
    """process() completes normally when no logger is provided."""
    llm_resp = _make_llm_response("email | inbound | update note", None)
    agent, _, _, _ = _make_agent(llm_response=llm_resp)

    result = await agent.process(
        inbound=_make_inbound(),
        resolution=_make_resolution_with_task("TSK-AB-0009-ATM"),
        agent_id="main",
        user_id="usr-001",
    )

    assert result.action_taken in ("node_updated", "both", "unmatched", "error")
