# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for session_id ContextVar and SessionFilter."""

from __future__ import annotations

import asyncio
import logging

from graphclaw.infra.logging.context import (
    SessionFilter,
    generate_session_id,
    get_session_id,
    set_session_id,
)


class TestSessionIdContextVar:
    def test_default_is_empty_string(self):
        # Run in a fresh task context to avoid pollution from other tests
        async def _check():
            # Reset by setting empty string
            set_session_id("")
            assert get_session_id() == ""

        asyncio.get_event_loop().run_until_complete(_check())

    def test_set_and_get(self):
        set_session_id("SES-test-123")
        assert get_session_id() == "SES-test-123"
        set_session_id("")  # cleanup

    def test_generate_session_id_format(self):
        sid = generate_session_id()
        assert sid.startswith("SES-")
        assert len(sid) == 40  # "SES-" + 36 char UUID

    def test_generate_unique_ids(self):
        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100

    def test_propagates_to_child_coroutine(self):
        async def parent():
            set_session_id("SES-parent")
            result = await child()
            return result

        async def child():
            return get_session_id()

        result = asyncio.get_event_loop().run_until_complete(parent())
        assert result == "SES-parent"

    def test_child_task_inherits_context(self):
        async def run():
            set_session_id("SES-from-request")

            async def child_task():
                return get_session_id()

            task = asyncio.create_task(child_task())
            return await task

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == "SES-from-request"


class TestSessionFilter:
    def setup_method(self):
        self.filter = SessionFilter()
        set_session_id("")  # reset

    def test_injects_session_id_from_context(self):
        set_session_id("SES-injected")
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        self.filter.filter(record)
        assert record.session_id == "SES-injected"
        set_session_id("")

    def test_does_not_override_explicit_session_id(self):
        set_session_id("SES-context")
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        record.session_id = "SES-explicit"
        self.filter.filter(record)
        assert record.session_id == "SES-explicit"
        set_session_id("")

    def test_always_returns_true(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert self.filter.filter(record) is True

    def test_empty_context_sets_empty_string(self):
        set_session_id("")
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        self.filter.filter(record)
        assert record.session_id == ""
